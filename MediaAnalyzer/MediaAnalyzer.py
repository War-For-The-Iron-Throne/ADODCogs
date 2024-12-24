import discord
from discord.ui import View, Button
from redbot.core import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re

class PaginatedEmbeds(View):
    """
    Single View-based paginator that cycles through pages,
    where each 'page' can have multiple embeds (sent as 'embeds=[...]').
    """
    def __init__(self, pages: list[list[discord.Embed]], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.pages = pages  # e.g. [ [embedA, embedB], [embedC], [embedD, embedE], ... ]
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user to use the paginator.
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        # Edit the original message to show the new page of embeds.
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
            # If we're already on the first page, we can't "respond" again with interaction.response;
            # use followup instead.
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

        We'll ensure we only capture the lines actually under "Installed Modules"
        so we don't mix in other headings like "Loaded BLSE Plugins," etc.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                # Use BeautifulSoup to remove any HTML tags, leaving only the text
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                # We'll define a pattern of possible section headings we might see,
                # so we can stop when the next heading appears.
                headings_pattern = (
                    r"(?:Exception|Enhanced Stacktrace|Installed Modules|"
                    r"Involved Modules and Plugins|Loaded BLSE Plugins|Assemblies|"
                    r"Native Assemblies|Harmony Patches|Log Files|Mini Dump|Save File|"
                    r"Screenshot|Screenshot Data|Json Model Data)"
                )

                # Regex for Exception section
                exception_regex = re.compile(
                    rf"[+-]\s*Exception\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                # Regex for Enhanced Stacktrace section
                stacktrace_regex = re.compile(
                    rf"[+-]\s*Enhanced Stacktrace\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                # Regex for Installed Modules section
                modules_regex = re.compile(
                    rf"[+-]\s*Installed Modules\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                exception_match = exception_regex.search(full_text)
                stacktrace_match = stacktrace_regex.search(full_text)
                modules_match = modules_regex.search(full_text)

                exception_text = exception_match.group(1).strip() if exception_match else ""
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""

                # Parse the lines under Installed Modules
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    # Capture every line that starts with "+" or "-"
                    # Example line: "+ Harmony (Bannerlord.Harmony, v2.3.3.207)"
                    # We don't cut off at the parenthesis so we can capture the entire line if needed:
                    mod_lines = re.findall(r"^[+\-]\s+(.*)$", modules_block, re.MULTILINE)

                    # Clean them up
                    mod_lines = [line.strip() for line in mod_lines if line.strip()]

                    # Join them with newline so each mod is on its own line
                    if mod_lines:
                        installed_modules_text = "\n".join(mod_lines)

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
        Break up a section's content into multiple embeds, if needed.
        - The first chunk has a title; subsequent chunks do not, to keep it "seamless."
        - Each chunk is placed in an embed field with code blocks.
        - Returns a list of embed objects that belong to this section.
        """
        content = content.strip()
        if not content:
            return []

        CHUNK_SIZE = 1024 - 10  # overhead for code block formatting etc.
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
        Build a list of PAGES, each page is a list of embed objects.
        We'll do:
          Page 1 => all 'Exception' embeds
          Page 2 => all 'Enhanced Stacktrace' embeds
          Page 3 => all 'Installed Modules' embeds

        If a section is empty, skip it entirely.
        If everything is empty, return a single page with "No data" embed.
        """
        exc_embeds = self.build_embeds_for_section("Exception", exception_text)
        stack_embeds = self.build_embeds_for_section("Enhanced Stacktrace", stacktrace_text)
        mods_embeds = self.build_embeds_for_section("Installed Modules", installed_modules_text)

        pages = []
        if exc_embeds:
            pages.append(exc_embeds)
        if stack_embeds:
            pages.append(stack_embeds)
        if mods_embeds:
            pages.append(mods_embeds)

        if not pages:  # If no data found at all
            empty_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[empty_embed]]

        return pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Command to analyze a crash report or an image link.
        Presents all sections in one message, multiple pages, each page can have multiple embeds.
        """
        # Build the pages first, so the user can flip through them without re-fetching.
        if "report.butr.link" in url.lower():
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            mods_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, mods_text)
            if len(pages) == 1:
                # Single page => possibly multiple embeds
                return await ctx.send(embeds=pages[0])

            view = PaginatedEmbeds(pages, ctx.author.id)
            # Send the first page with the paginator
            await ctx.send(embeds=pages[0], view=view)
        else:
            # Otherwise, treat the URL as an image to be analyzed by pytesseract
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
        """Auto-detect crash report links in messages and process them similarly."""
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

        # If no recognized crash-report link, do nothing special here.

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
