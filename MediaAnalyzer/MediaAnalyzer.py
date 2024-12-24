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

def ensure_under_6000(embed: discord.Embed) -> bool:
    """
    Check if a single embed's total size (title + description + fields + etc.)
    might exceed Discord's 6000-character limit.

    We'll convert the embed to a dict and measure its JSON length
    as a rough proxy. This is slightly more strict than needed, but safe.
    """
    try:
        embed_dict = embed.to_dict()
        json_str = json.dumps(embed_dict)
        return len(json_str) <= 6000
    except Exception:
        # If something goes wrong converting, err on the side of "unsafe"
        return False

def build_safe_embeds(section_name: str, content: str) -> list[discord.Embed]:
    """
    Safely create multiple embeds from a single large text block (content).
    Each embed will contain a *single field*, ensuring:
      - Field value <= 1024
      - Total embed size <= 6000
      - We also sanitize triple-backticks to avoid weird expansions
    """
    content = content.strip()
    if not content:
        return []

    # Replace triple backticks with something safer
    # so we don't cause embedded code blocks in code blocks.
    safe_text = content.replace("```", "'''")

    # We'll start with a chunk size that comfortably fits in a single field.
    # If your text is extremely large, we'll keep chunking until < 1024.
    chunk_size = 1000

    # Break the text into 1000-character pieces (just below the 1024 field limit).
    # If this STILL ends up making an embed that is too big (rare), we'll reduce further.
    chunks = [safe_text[i : i + chunk_size] for i in range(0, len(safe_text), chunk_size)]

    embeds = []
    for idx, chunk_text in enumerate(chunks, start=1):
        # For the first chunk, embed title = section_name, otherwise blank
        embed_title = section_name if idx == 1 else ""
        embed = discord.Embed(title=embed_title, color=discord.Color.blue())

        # We'll wrap the chunk in triple backticks
        field_value = f"```{chunk_text}```"

        embed.add_field(name="\u200b", value=field_value, inline=False)

        # If for some reason it's still too large, we keep chopping it down
        # until it fits under the 6000 limit (very rare scenario).
        while not ensure_under_6000(embed) and len(chunk_text) > 50:
            # Reduce chunk_text further
            chunk_text = chunk_text[: len(chunk_text) // 2]
            embed.clear_fields()  # Remove existing fields
            field_value = f"```{chunk_text}```"
            embed.add_field(name="\u200b", value=field_value, inline=False)

        # If we STILL can't fit, we give up on this chunk and skip it
        if not ensure_under_6000(embed):
            continue

        embeds.append(embed)

    return embeds

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

        1) Build embed-lists for each section (Exception, Enhanced Stacktrace, Modules)
           using build_safe_embeds(...).
        2) Then chunk them into groups of 10 for pagination.
        3) Return the "pages" - a list of lists of embeds.
        """
        exc_embeds = build_safe_embeds("Exception", exception_text)
        stack_embeds = build_safe_embeds("Enhanced Stacktrace", stacktrace_text)
        mods_embeds = build_safe_embeds("Installed Modules", installed_modules_text)

        all_embeds = exc_embeds + stack_embeds + mods_embeds

        # If no sections had anything, show a single 'No Data' page
        if not all_embeds:
            empty_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[empty_embed]]

        # Now we chunk the entire list of built embeds in groups of 10
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
