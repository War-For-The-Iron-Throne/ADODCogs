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
        # Because we've already sent a response in the command,
        # we should edit the message or use followup here
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
            # If we're already on the first page, we can't do a new "interaction.response.send_message"
            # because we've already responded. We can do:
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

        In some crash reports, there are many sections (Involved Modules, BLSE Plugins, etc.).
        We'll stop capturing the Installed Modules section whenever we see the next known
        heading so it doesn't mix in all those other categories.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                # Just get the text for regex-based extraction
                full_text = soup.get_text()

                # We'll define big "OR" patterns for what headings to stop at.
                # That way, "Installed Modules" won't keep grabbing "Loaded BLSE Plugins" etc.
                headings_pattern = (
                    r"(?:Exception|Enhanced Stacktrace|Installed Modules|"
                    r"Involved Modules and Plugins|Loaded BLSE Plugins|Assemblies|"
                    r"Native Assemblies|Harmony Patches|Log Files|Mini Dump|Save File|"
                    r"Screenshot|Screenshot Data|Json Model Data)"
                )

                # 1) Exception
                # Stop at the next recognized heading or end-of-file:
                exception_regex = re.compile(
                    rf"[+-]\s*Exception\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                # 2) Enhanced Stacktrace
                stacktrace_regex = re.compile(
                    rf"[+-]\s*Enhanced Stacktrace\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                # 3) Installed Modules
                modules_regex = re.compile(
                    rf"[+-]\s*Installed Modules\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                exception_match = exception_regex.search(full_text)
                exception_text = exception_match.group(1).strip() if exception_match else ""

                stacktrace_match = stacktrace_regex.search(full_text)
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""

                modules_match = modules_regex.search(full_text)
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    # We only want lines that look like "+ Something (Ident, version)" or "- Something..."
                    # So let's grab lines that begin with + or -, then the mod name, ignoring parentheses
                    # up until we hit them or the line's end:
                    mod_lines = re.findall(r"^[+-]\s+(.*?)(?:\(|$)", modules_block, re.MULTILINE)
                    # Clean them up
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

        # If no data found at all
        if not pages:
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

        - If it's a crash report from 'report.butr.link', parse the text for
          Exception / Enhanced Stacktrace / Installed Modules, then display them in a paginator.
        - Otherwise, treat the URL as an image link to analyze with pytesseract.
        """
        # Build all pages *first*, then send the first embed with the paginator.
        if "report.butr.link" in url.lower():
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            mods_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, mods_text)
            # If there's only one page, no next/prev
            if len(pages) == 1:
                return await ctx.send(embeds=pages[0])

            # Otherwise, use our paginator
            view = PaginatedEmbeds(pages, ctx.author.id)
            # Because the pages are already built, pressing Next won't cause a re-fetch or anything.
            await ctx.send(embeds=pages[0], view=view)

        else:
            # Otherwise, assume it's an image to analyze (OCR)
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
        Auto-detect crash report links in messages, build all pages,
        and display them if a recognized link is found.
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
                    # Single page => possibly multiple embeds
                    await message.reply(embeds=pages[0])
                else:
                    view = PaginatedEmbeds(pages, message.author.id)
                    await message.reply(embeds=pages[0], view=view)

                return

        # If no crash-report link found, do nothing special here (optional).

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
