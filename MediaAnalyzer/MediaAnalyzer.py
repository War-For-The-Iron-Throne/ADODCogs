import discord
from discord.ui import View, Button
from redbot.core import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re

def chunk_embeds(embeds: list[discord.Embed], size=10) -> list[list[discord.Embed]]:
    """
    Discord only allows up to 10 embeds in a single message.
    So if we have more than 10, we split them into multiple 'pages.'
    """
    chunks = []
    for i in range(0, len(embeds), size):
        chunks.append(embeds[i : i + size])
    return chunks

class PaginatedEmbeds(View):
    """
    A paginator that cycles through *pages*, where each page is
    a list of up to 10 discord.Embed objects.
    """
    def __init__(self, pages: list[list[discord.Embed]], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.pages = pages  # e.g. [ [embed1, embed2...], [embedX, embedY...] ]
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user to use the paginator.
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        # We must edit the original message with up to 10 embeds:
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
            # Already on first page, can't go back further.
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

class MediaAnalyzer(commands.Cog):
    """Analyze images, crash reports, and webpages for AI Assistant functionality."""

    def __init__(self, bot):
        self.bot = bot
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
        We'll parse the text, locate these sections, and stop if we see
        any other known headings (so it doesn't mix them together).
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

                    # Grab every line that starts with + or -, ignoring parentheses,
                    # so we get each mod's display line:
                    mod_lines = re.findall(
                        r"^[+-]\s+(.*?)(?:\(|$)",
                        modules_block,
                        re.MULTILINE
                    )
                    # Clean up the mod lines:
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
        """Analyze image bytes with pytesseract."""
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

    def build_embeds_for_section(self, section_name: str, content: str) -> list[discord.Embed]:
        """
        Break up a section's content into multiple Embeds (1k chunk each),
        so no single embed field is too large.
        """
        content = content.strip()
        if not content:
            return []

        CHUNK_SIZE = 1024 - 10  # overhead for code block formatting
        chunks = []
        start_idx = 0
        while start_idx < len(content):
            end_idx = min(start_idx + CHUNK_SIZE, len(content))
            chunk = content[start_idx:end_idx]
            chunks.append(chunk)
            start_idx += CHUNK_SIZE

        embeds = []
        for i, chunk_text in enumerate(chunks, start=1):
            embed_title = section_name if i == 1 else ""
            embed = discord.Embed(title=embed_title, color=discord.Color.blue())
            embed.add_field(
                name="",
                value=f"```{chunk_text}```",
                inline=False
            )
            embeds.append(embed)

        return embeds

    def build_pages(
        self,
        exception_text: str,
        stacktrace_text: str,
        installed_modules_text: str
    ) -> list[list[discord.Embed]]:
        """
        Build a list of 'pages.' Each page is a list of up to 10 Embeds.

        Steps:
          1) Build embed-lists for each section (Exception, Enhanced Stacktrace, etc.)
          2) If an embed-list has more than 10, chunk it further.
          3) Each chunk is a 'page.'
        """
        # Build each section's embed objects
        exc_embeds = self.build_embeds_for_section("Exception", exception_text)
        stack_embeds = self.build_embeds_for_section("Enhanced Stacktrace", stacktrace_text)
        mods_embeds = self.build_embeds_for_section("Installed Modules", installed_modules_text)

        # We'll create a final pages list
        all_pages = []

        # For each section, chunk its embeds in groups of 10.
        if exc_embeds:
            for chunked in chunk_embeds(exc_embeds, 10):
                all_pages.append(chunked)

        if stack_embeds:
            for chunked in chunk_embeds(stack_embeds, 10):
                all_pages.append(chunked)

        if mods_embeds:
            for chunked in chunk_embeds(mods_embeds, 10):
                all_pages.append(chunked)

        if not all_pages:
            empty_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[empty_embed]]

        return all_pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Command to analyze a crash report or an image link.
        If it's a known crash report link, parse the sections. Otherwise, try to OCR an image.
        """
        if "report.butr.link" in url.lower():
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            mods_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, mods_text)
            # If there's only one page, no next/prev needed
            if len(pages) == 1:
                return await ctx.send(embeds=pages[0])

            # Use paginator
            view = PaginatedEmbeds(pages, ctx.author.id)
            await ctx.send(embeds=pages[0], view=view)

        else:
            # Attempt to OCR an image
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
        Auto-detect crash report links in messages.
        Build all pages and send them if found.
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
        # Otherwise do nothing if no crash links.

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
