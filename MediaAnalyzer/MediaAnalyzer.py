import json
import discord
from discord.ui import View, Button
from redbot.core import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re

###############################################################################
# Helper Functions
###############################################################################

def embed_size(embed: discord.Embed) -> int:
    """
    Convert an embed to a dict, then JSON-stringify it to measure
    its total size. We'll use this to ensure it doesn't exceed 6000.
    """
    as_dict = embed.to_dict()
    return len(json.dumps(as_dict))

def build_embeds_for_section(section_name: str, content: str) -> list[discord.Embed]:
    """
    Break 'content' into multiple embeds (each with up to 10 fields, each field <= 1000 chars).
    We also ensure that each embed doesn't exceed 6000 total JSON-serialized characters.

    Steps:
      1. Sanitize triple backticks (replace them).
      2. Wrap the text by splitting into lines of ~80 chars.
      3. Accumulate lines into a 'current_field' until:
         - adding another line would exceed 1000 chars, or
         - we have 10 fields in the embed, or
         - adding the field would exceed the embed's 6000-char limit.
      4. Move to the next embed as needed.
    """
    content = content.strip()
    if not content:
        return []

    # Replace triple backticks so we don't nest code blocks
    content = content.replace("```", "'''")

    # We'll break content into lines no longer than ~80 chars
    # so we can easily accumulate them into fields.
    lines = []
    for raw_line in content.splitlines():
        raw_line = raw_line.strip("\r")
        # further split if the line is very long
        while len(raw_line) > 80:
            lines.append(raw_line[:80])
            raw_line = raw_line[80:]
        if raw_line:
            lines.append(raw_line)

    # We'll build one embed at a time. Start a fresh embed.
    embeds = []
    embed = discord.Embed(title=section_name, color=discord.Color.blue())
    field_count = 0
    current_field_lines = []
    first_field_added = False  # We'll clear the title if we've already used it

    def finalize_field():
        """Helper to finalize the current_field_lines into the embed as a single field."""
        nonlocal embed, field_count, current_field_lines
        field_text = "\n".join(current_field_lines)
        # wrap with code fences
        field_text = f"```{field_text}```"
        embed.add_field(name="\u200b", value=field_text, inline=False)
        field_count += 1
        current_field_lines.clear()

    for line in lines:
        # Check if adding `line` to current_field_lines would exceed 1000
        tentative_field_text = "\n".join(current_field_lines + [line])
        if len(tentative_field_text) > 1000:
            # finalize the current field, start a new one
            if current_field_lines:
                finalize_field()

            # If we already have 10 fields, or adding the new field to this embed
            # would exceed 6000, we start a new embed
            if field_count >= 10 or embed_size(embed) > 6000:
                embeds.append(embed)
                # Start a new embed, possibly without a title if it's not the first embed
                embed = discord.Embed(color=discord.Color.blue())
                field_count = 0

                # Only the first embed in the section has the section_name as a title
                if not first_field_added:
                    embed.title = section_name
                    first_field_added = True

            current_field_lines.append(line)
            continue

        # If it doesn't exceed 1000, we just accumulate the line
        current_field_lines.append(line)

        # After adding the line, check if we already have 10 fields or if adding
        # another line might blow up the embed size. Let's do it carefully:
        if field_count >= 10:
            # This means we can't add any more fields to this embed
            if current_field_lines:
                finalize_field()
            embeds.append(embed)
            # new embed
            embed = discord.Embed(color=discord.Color.blue())
            field_count = 0
            if not first_field_added:
                embed.title = section_name
                first_field_added = True
            continue

    # After the loop, if there's anything left in current_field_lines, finalize it
    if current_field_lines:
        finalize_field()

    # We might end with an embed that has no fields, so check embed_size > (base embed)
    # but typically we'll at least have something. We'll only append if it actually has fields.
    if field_count > 0:
        # If for any reason it exceeds 6000 after adding the final field, we make a new embed
        if embed_size(embed) > 6000:
            # Move that big field to a new embed
            last_field = embed.fields[-1]
            embed.remove_field(len(embed.fields) - 1)
            # If the embed is still non-empty, store it
            if len(embed.fields) > 0:
                embeds.append(embed)

            # Start a fresh embed
            embed = discord.Embed(color=discord.Color.blue())
            if not first_field_added:
                embed.title = section_name
            embed.add_field(name=last_field.name, value=last_field.value, inline=False)
            # final size check
            if embed_size(embed) <= 6000:
                embeds.append(embed)
        else:
            embeds.append(embed)

    return embeds

def chunk_embeds(embeds: list[discord.Embed], size=10) -> list[list[discord.Embed]]:
    """
    Discord only allows up to 10 embeds in a single message.
    We split a list of Embeds into sub-lists of up to 'size'.
    Each sub-list is one "page" in the paginator.
    """
    pages = []
    for i in range(0, len(embeds), size):
        pages.append(embeds[i : i + size])
    return pages

###############################################################################
# Paginator
###############################################################################

class PaginatedEmbeds(View):
    """
    A paginator that cycles through 'pages,' where each page
    is a list of up to 10 discord.Embed objects.
    """
    def __init__(self, pages: list[list[discord.Embed]], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.pages = pages
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user to use this paginator.
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        # We'll edit the original message with the new embeds
        await interaction.response.edit_message(
            embeds=self.pages[self.index],
            view=self
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.index > 0:
            self.index -= 1
            await self.update_message(interaction)
        else:
            await interaction.followup.send(
                "You're already on the first page!", ephemeral=True
            )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
            await self.update_message(interaction)
        else:
            await interaction.followup.send(
                "You're already on the last page!", ephemeral=True
            )

###############################################################################
# The Cog
###############################################################################

class MediaAnalyzer(commands.Cog):
    """Analyze images, crash reports, and webpages for AI Assistant functionality."""

    def __init__(self, bot):
        self.bot = bot
        # We'll create an aiohttp session to fetch the webpage for crash reports.
        self.session = aiohttp.ClientSession()

    async def cog_load(self) -> None:
        print("MediaAnalyzer cog has been loaded successfully.")

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()
        print("MediaAnalyzer cog has been unloaded and resources cleaned up.")

    async def fetch_webpage(self, url: str) -> dict:
        """
        Fetch the content of a crash-report webpage and extract:
          - Exception
          - Enhanced Stacktrace
          - Installed Modules

        We'll parse the text with BeautifulSoup, then use regex to locate
        the relevant sections, stopping if we see other known headings.

        Returns a dict with keys: ["exception", "enhanced_stacktrace", "installed_modules"].
        If an error occurs, returns {"error": "..."}.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                # We'll define an 'OR' pattern of headings to stop at:
                headings_pattern = (
                    r"(?:Exception|Enhanced Stacktrace|Installed Modules|"
                    r"Loaded BLSE Plugins|Involved Modules and Plugins|Assemblies|"
                    r"Native Assemblies|Harmony Patches|Log Files|Mini Dump|Save File|"
                    r"Screenshot|Screenshot Data|Json Model Data)"
                )

                # Regex for each section:
                exception_regex = re.compile(
                    rf"[+-]\s*Exception\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )
                stacktrace_regex = re.compile(
                    rf"[+-]\s*Enhanced Stacktrace\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )
                modules_regex = re.compile(
                    rf"[+-]\s*Installed Modules\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                # 1) Exception:
                exception_match = exception_regex.search(full_text)
                exception_text = exception_match.group(1).strip() if exception_match else ""

                # 2) Enhanced Stacktrace:
                stacktrace_match = stacktrace_regex.search(full_text)
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""

                # 3) Installed Modules:
                modules_match = modules_regex.search(full_text)
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    # We'll capture lines starting with + or - up to '(' or line-end
                    mod_lines = re.findall(
                        r"^[+-]\s+(.*?)(?:\(|$)",
                        modules_block,
                        re.MULTILINE
                    )
                    mod_names = [m.strip() for m in mod_lines if m.strip()]
                    if mod_names:
                        installed_modules_text = "\n".join(mod_names)

                return {
                    "exception": exception_text,
                    "enhanced_stacktrace": stacktrace_text,
                    "installed_modules": installed_modules_text
                }

        except Exception as e:
            return {"error": f"Error fetching webpage: {e}"}

    async def analyze_media(self, image_data: bytes) -> dict:
        """Analyze image bytes with pytesseract (OCR)."""
        try:
            image = Image.open(BytesIO(image_data))
            text = pytesseract.image_to_string(image)
            return {
                "text": text.strip(),
                "width": image.width,
                "height": image.height,
            }
        except Exception as e:
            return {"error": f"Failed to analyze image: {e}"}

    def build_pages(
        self,
        exception_text: str,
        stacktrace_text: str,
        installed_modules_text: str
    ) -> list[list[discord.Embed]]:
        """
        Build a list of 'pages.' Each page = up to 10 Embeds.

        For each section (Exception, Enhanced Stacktrace, Modules),
        we create multiple embeds via build_embeds_for_section(...).
        Then we combine all those embeds into a single list, and chunk them
        in groups of 10 to form the final "pages" for pagination.
        """
        exc_embeds = build_embeds_for_section("Exception", exception_text)
        stack_embeds = build_embeds_for_section("Enhanced Stacktrace", stacktrace_text)
        mods_embeds = build_embeds_for_section("Installed Modules", installed_modules_text)

        all_embeds = exc_embeds + stack_embeds + mods_embeds
        if not all_embeds:
            # If no data was found at all, return a single "No Data" page
            empty_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[empty_embed]]

        pages = chunk_embeds(all_embeds, 10)
        return pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Command to analyze a crash report or an image link.
        """
        # If it's a known crash report
        if "report.butr.link" in url.lower():
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            mods_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, mods_text)
            # If there's only 1 page, no need for pagination
            if len(pages) == 1:
                return await ctx.send(embeds=pages[0])

            # Otherwise, create the paginator
            view = PaginatedEmbeds(pages, ctx.author.id)
            await ctx.send(embeds=pages[0], view=view)

        else:
            # Otherwise, assume it's an image link to be OCR'ed
            try:
                async with self.session.get(url) as response:
                    if response.status != 200:
                        return await ctx.send("Failed to fetch the media from the URL.")
                    image_data = await response.read()
                    analysis = await self.analyze_media(image_data)
                    if "error" in analysis:
                        return await ctx.send(f"Error: {analysis['error']}")

                    embed = discord.Embed(
                        title="Media Analysis",
                        description=f"Content extracted from the media at {url}",
                        color=discord.Color.green(),
                    )
                    embed.add_field(
                        name="Text Content",
                        value=analysis["text"] or "No text found",
                        inline=False,
                    )
                    embed.add_field(
                        name="Resolution",
                        value=f"{analysis['width']}x{analysis['height']}",
                        inline=False,
                    )
                    await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(f"Error analyzing the media: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Auto-detect crash report links in messages, parse them,
        and show the results in multiple pages if necessary.
        """
        if message.author.bot:
            return

        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url.lower():
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                exception_text = data.get("exception", "")
                stacktrace_text = data.get("enhanced_stacktrace", "")
                mods_text = data.get("installed_modules", "")

                pages = self.build_pages(exception_text, stacktrace_text, mods_text)
                if len(pages) == 1:
                    await message.reply(embeds=pages[0])
                else:
                    view = PaginatedEmbeds(pages, message.author.id)
                    await message.reply(embeds=pages[0], view=view)

                return
        # Otherwise do nothing if no crash link.

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
