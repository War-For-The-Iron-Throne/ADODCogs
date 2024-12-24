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
    A single View-based paginator that cycles through pages,
    where each 'page' can be a *list* of embed objects.
    """
    def __init__(self, pages: list[list[discord.Embed]], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout, adjust as needed
        self.pages = pages  # e.g. [ [embed1, embed2], [embed3], [embed4, embed5] ]
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Restrict button usage to the original user."""
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        """Edits the existing message to show the new set of embeds."""
        await interaction.response.edit_message(
            embeds=self.pages[self.index],  # list of embed objects
            view=self
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.index > 0:
            self.index -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.send_message(
                "You're already on the first page!", ephemeral=True
            )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
            await self.update_message(interaction)
        else:
            await interaction.response.send_message(
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
        - Installed Modules (just mod names)
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}
                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                # Regex for each section
                exception_match = re.search(
                    r"\+ Exception\s+(.+?)(?=\n\n|\Z)", full_text, re.DOTALL
                )
                stacktrace_match = re.search(
                    r"\+ Enhanced Stacktrace\s+(.+?)(?=\n\n|\Z)", full_text, re.DOTALL
                )
                modules_match = re.search(
                    r"\+ Installed Modules\s+(.+?)(?=\n\n|\Z)", full_text, re.DOTALL
                )

                exception_text = exception_match.group(1).strip() if exception_match else ""
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""

                # Extract only mod names
                installed_modules_text = ""
                if modules_match:
                    raw_mods = modules_match.group(1).strip()
                    lines = raw_mods.splitlines()
                    mod_names = []
                    for line in lines:
                        line = line.strip()
                        if line.startswith("+ "):
                            # get everything after '+ ' up to '(' or the end
                            m = re.match(r"\+\s+(.+?)(?:\(|$)", line)
                            if m:
                                mod_names.append(m.group(1).strip())

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
        """Analyzes media data for text and content."""
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

    def build_embeds_for_section(self, section_title: str, content: str) -> list[discord.Embed]:
        """
        Given a big block of text for a single section (e.g. "Exception"),
        chunk it into multiple embeds (if needed). Each embed gets a code block field,
        respecting the 1024-char-per-field limit.
        """
        content = content.strip()
        if not content:
            return []

        # Discord limit for embed field is 1024 chars.
        # We'll subtract ~10 chars for the code block formatting and potential newlines.
        CHUNK_SIZE = 1024 - 10

        # Break content into CHUNK_SIZE segments.
        chunks = []
        start_idx = 0
        while start_idx < len(content):
            end_idx = min(start_idx + CHUNK_SIZE, len(content))
            chunks.append(content[start_idx:end_idx])
            start_idx += CHUNK_SIZE

        # Build an embed for each chunk
        embeds = []
        for i, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title=f"{section_title} (Chunk {i}/{len(chunks)})",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Details",
                value=f"```{chunk}```",
                inline=False
            )
            embeds.append(embed)

        return embeds

    def build_all_pages(
        self,
        exception_text: str,
        stacktrace_text: str,
        installed_modules_text: str
    ) -> list[list[discord.Embed]]:
        """
        Builds a list of "pages", where each page is a *list of embed objects*.

        For example:
          Page 1: [exceptionEmbed1, exceptionEmbed2, ...]
          Page 2: [stacktraceEmbed1, stacktraceEmbed2, ...]
          Page 3: [modulesEmbed1, modulesEmbed2, ...]

        If any section is empty, we skip it entirely.
        If absolutely everything is empty, we produce a single page with "No data."
        """
        # Build embeds for each section
        exception_embeds = self.build_embeds_for_section("Exception", exception_text)
        stacktrace_embeds = self.build_embeds_for_section("Enhanced Stacktrace", stacktrace_text)
        modules_embeds = self.build_embeds_for_section("Installed Modules", installed_modules_text)

        pages = []
        if exception_embeds:
            pages.append(exception_embeds)  # e.g. [embed1, embed2, ...]
        if stacktrace_embeds:
            pages.append(stacktrace_embeds)
        if modules_embeds:
            pages.append(modules_embeds)

        # If all are empty, produce a single page with a "No data" embed
        if not pages:
            no_data_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[no_data_embed]]

        return pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Command to analyze a crash report or an image link.
        - Crash report: single message, multiple embeds, paginated with Next/Prev.
        - Image link: standard embed with OCR results.
        """
        if "report.butr.link" in url:
            data = await self.fetch_webpage(url)
            if "error" in data:
                await ctx.send(data["error"])
                return

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            modules_text = data.get("installed_modules", "")

            pages = self.build_all_pages(exception_text, stacktrace_text, modules_text)

            # pages is a list of "page," where each page is a list of embed objects
            # If only one page, we don't need button-based pagination
            if len(pages) == 1:
                # Also check if there's only 1 embed in that single page
                if len(pages[0]) == 1:
                    return await ctx.send(embed=pages[0][0])
                else:
                    # Send multiple embeds at once (same message), no pagination needed
                    return await ctx.send(embeds=pages[0])

            # If more than one page, we do button-based pagination
            # We'll start on page 0
            view = PaginatedEmbeds(pages, ctx.author.id)
            await ctx.send(embeds=pages[0], view=view)
        else:
            # Handle image analysis
            try:
                async with self.session.get(url) as response:
                    if response.status != 200:
                        await ctx.send("Failed to fetch the media from the URL.")
                        return
                    image_data = await response.read()
                    analysis = await self.analyze_media(image_data)
                    if "error" in analysis:
                        await ctx.send(f"Error: {analysis['error']}")
                    else:
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
        """Auto-detect crash report links or handle attachments."""
        if message.author.bot:
            return

        # Check for crash-report links
        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url:
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                exception_text = data.get("exception", "")
                stacktrace_text = data.get("enhanced_stacktrace", "")
                modules_text = data.get("installed_modules", "")

                pages = self.build_all_pages(exception_text, stacktrace_text, modules_text)

                if len(pages) == 1:
                    # Single page => maybe multiple embeds
                    if len(pages[0]) == 1:
                        # Only 1 embed in that page
                        await message.reply(embed=pages[0][0])
                    else:
                        await message.reply(embeds=pages[0])
                else:
                    view = PaginatedEmbeds(pages, message.author.id)
                    await message.reply(embeds=pages[0], view=view)

                return  # Stop once we've handled the link

        # If not a crash-report link, check attachments
        if message.attachments:
            for attachment in message.attachments:
                # Image analysis
                if attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    try:
                        image_data = await attachment.read()
                        analysis = await self.analyze_media(image_data)
                        if "error" in analysis:
                            await message.reply(f"Error: {analysis['error']}")
                        else:
                            embed = discord.Embed(
                                title="Media Analysis",
                                description="Content extracted from the uploaded image.",
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
                            await message.reply(embed=embed)
                    except Exception as e:
                        await message.reply(f"Error analyzing the image: {e}")

                # HTML crash-report file
                elif attachment.filename.lower().endswith('.html'):
                    try:
                        html_bytes = await attachment.read()
                        html_content = html_bytes.decode('utf-8')
                        soup = BeautifulSoup(html_content, 'html.parser')
                        text = soup.get_text()
                        # Just a simple single embed:
                        embed = discord.Embed(
                            title="Crash Report Analysis (HTML)",
                            description="Extracted text from the uploaded HTML crash report.",
                            color=discord.Color.blue(),
                        )
                        truncated = text[:1024]  # Just to keep it safe
                        embed.add_field(name="Extracted Content", value=truncated, inline=False)
                        await message.reply(embed=embed)
                    except Exception as e:
                        await message.reply(f"Error processing HTML file: {e}")


async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
