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
import copy

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

def build_embeds_for_section(
    section_name: str,
    content: str,
    max_line_len: int = 50,
    max_field_chars: int = 500,
    max_fields_per_embed: int = 5
) -> list[discord.Embed]:
    """
    Break 'content' into multiple embeds, ensuring:
      - each line is <= max_line_len chars,
      - each field is <= max_field_chars,
      - each embed has <= max_fields_per_embed fields,
      - each embed's total JSON is <= 6000.

    We'll accumulate lines in 'current_field' until:
      1. Adding a new line would exceed max_field_chars, or
      2. We've reached max_fields_per_embed, or
      3. Adding the new field would exceed the 6000-char JSON limit.
    Then we'll start a new embed or field accordingly.
    """
    content = content.strip()
    if not content:
        return []

    # Replace triple backticks so we don't nest code blocks
    content = content.replace("```", "'''")

    # 1) Break the text into lines no longer than max_line_len
    lines = []
    for raw_line in content.splitlines():
        raw_line = raw_line.strip("\r")
        while len(raw_line) > max_line_len:
            lines.append(raw_line[:max_line_len])
            raw_line = raw_line[max_line_len:]
        if raw_line:
            lines.append(raw_line)

    # Prepare to build embeds
    embeds = []
    embed = discord.Embed(title=section_name, color=discord.Color.blue())
    used_title = False  # The first embed in each section gets a title; subsequent do not
    field_count = 0
    current_field_lines = []

    def finalize_field(embed_obj: discord.Embed, field_lines: list[str]) -> bool:
        """
        Convert field_lines -> a single code-block field, then test adding it to embed_obj.
        Return True if it fits, False if a new embed is needed.
        """
        field_text = "\n".join(field_lines)
        field_text = f"```{field_text}```"

        test_embed = copy.deepcopy(embed_obj)
        test_embed.add_field(name="\u200b", value=field_text, inline=False)

        if embed_size(test_embed) <= 6000:
            embed_obj.add_field(name="\u200b", value=field_text, inline=False)
            return True
        return False

    for line in lines:
        new_field_text = "\n".join(current_field_lines + [line])
        # 2) If adding this line exceeds max_field_chars, finalize the current field
        if len(new_field_text) > max_field_chars:
            if current_field_lines:
                added_ok = finalize_field(embed, current_field_lines)
                if not added_ok:
                    # If it didn't fit in the current embed, push this embed if it has content
                    if field_count > 0 or (embed.title and embed.description):
                        embeds.append(embed)
                    embed = discord.Embed(color=discord.Color.blue())
                    if not used_title:
                        embed.title = section_name
                        used_title = True
                    finalize_field(embed, current_field_lines)

                field_count = len(embed.fields)

            current_field_lines = [line]

            # If we've reached max_fields_per_embed, push and start a new embed
            if field_count >= max_fields_per_embed:
                embeds.append(embed)
                embed = discord.Embed(color=discord.Color.blue())
                if not used_title:
                    embed.title = section_name
                    used_title = True
                field_count = 0
            continue

        # Otherwise, accumulate the line
        current_field_lines.append(line)
        # If we already have max_fields_per_embed, we must finalize now
        if field_count >= max_fields_per_embed:
            # finalize the current field
            if current_field_lines:
                added_ok = finalize_field(embed, current_field_lines)
                if not added_ok:
                    if field_count > 0 or (embed.title and embed.description):
                        embeds.append(embed)
                    embed = discord.Embed(color=discord.Color.blue())
                    if not used_title:
                        embed.title = section_name
                        used_title = True
                    finalize_field(embed, current_field_lines)
                field_count = len(embed.fields)
                current_field_lines = []

            # push embed
            embeds.append(embed)
            embed = discord.Embed(color=discord.Color.blue())
            if not used_title:
                embed.title = section_name
                used_title = True
            field_count = 0

    # After the loop, if there's anything left in current_field_lines, finalize it
    if current_field_lines:
        added_ok = finalize_field(embed, current_field_lines)
        if not added_ok:
            # if we couldn't fit it in the current embed, push the old embed first
            if field_count > 0 or (embed.title and embed.description):
                embeds.append(embed)
            embed = discord.Embed(color=discord.Color.blue())
            if not used_title:
                embed.title = section_name
                used_title = True
            finalize_field(embed, current_field_lines)
            field_count = len(embed.fields)
        else:
            field_count = len(embed.fields)

    # push the last embed if it has fields
    if len(embed.fields) > 0 or (embed.title and embed.description):
        if embed_size(embed) <= 6000:
            embeds.append(embed)
        else:
            # extremely unlikely with our small chunk sizes
            pass

    return embeds

def chunk_embeds(embeds: list[discord.Embed], size=5) -> list[list[discord.Embed]]:
    """
    Discord only allows up to 10 embeds in a single message,
    but since we want to be extra conservative, we chunk at 'size=5'.
    So each "page" will have up to 5 embeds.
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
    A simple paginator that cycles through 'pages' (each page is a list of up to 5 Embeds).
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
        self.session = aiohttp.ClientSession()

    async def cog_load(self):
        print("MediaAnalyzer cog loaded successfully.")

    async def cog_unload(self):
        if self.session:
            await self.session.close()
        print("MediaAnalyzer cog unloaded and resources cleaned up.")

    async def fetch_webpage(self, url: str) -> dict:
        """
        Fetch a crash-report webpage and parse out:
          - Exception
          - Enhanced Stacktrace
          - Installed Modules
        Return them in a dict or {"error": "..."} if something goes wrong.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                headings_pattern = (
                    r"(?:Exception|Enhanced Stacktrace|Installed Modules|"
                    r"Loaded BLSE Plugins|Involved Modules and Plugins|Assemblies|"
                    r"Native Assemblies|Harmony Patches|Log Files|Mini Dump|Save File|"
                    r"Screenshot|Screenshot Data|Json Model Data)"
                )

                # Use regex to find each section
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

                exception_match = exception_regex.search(full_text)
                stacktrace_match = stacktrace_regex.search(full_text)
                modules_match = modules_regex.search(full_text)

                exception_text = exception_match.group(1).strip() if exception_match else ""
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    mod_lines = re.findall(r"^[+-]\s+(.*?)(?:\(|$)", modules_block, re.MULTILINE)
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
        """Analyze an image (OCR) using pytesseract."""
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
        Build pages for the crash report by:
          1) Building multiple small embeds for each section,
          2) Combining them into a single list,
          3) Splitting them into pages of up to 5 embeds each.
        """
        exc_embeds = build_embeds_for_section("Exception", exception_text,
                                              max_line_len=50, max_field_chars=500, max_fields_per_embed=5)
        stack_embeds = build_embeds_for_section("Enhanced Stacktrace", stacktrace_text,
                                                max_line_len=50, max_field_chars=500, max_fields_per_embed=5)
        mods_embeds = build_embeds_for_section("Installed Modules", installed_modules_text,
                                               max_line_len=50, max_field_chars=500, max_fields_per_embed=5)
        all_embeds = exc_embeds + stack_embeds + mods_embeds

        if not all_embeds:
            # No data found
            empty_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[empty_embed]]

        pages = chunk_embeds(all_embeds, size=5)
        return pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Analyze either a crash report (if it includes "report.butr.link")
        or an image URL (OCR).
        """
        if "report.butr.link" in url.lower():
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            installed_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, installed_text)
            if len(pages) == 1:
                return await ctx.send(embeds=pages[0])
            else:
                view = PaginatedEmbeds(pages, ctx.author.id)
                await ctx.send(embeds=pages[0], view=view)
        else:
            # Possibly an image link
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
                        description=f"Content extracted from {url}",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Text Content", value=analysis["text"] or "No text found", inline=False)
                    embed.add_field(name="Resolution", value=f"{analysis['width']}x{analysis['height']}", inline=False)
                    await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(f"Error analyzing the media: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Auto-detect crash report links in messages and parse them."""
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
                installed_text = data.get("installed_modules", "")

                pages = self.build_pages(exception_text, stacktrace_text, installed_text)
                if len(pages) == 1:
                    await message.reply(embeds=pages[0])
                else:
                    view = PaginatedEmbeds(pages, message.author.id)
                    await message.reply(embeds=pages[0], view=view)
                return
        # Otherwise do nothing.

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
