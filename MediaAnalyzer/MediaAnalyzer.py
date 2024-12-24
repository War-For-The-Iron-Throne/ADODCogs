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
    A View that handles 'Previous' and 'Next' button presses to flip through a unified list of pages.
    """
    def __init__(self, embeds, invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.embeds = embeds
        self.index = 0
        self.invoker_id = invoker_id  # So only the command/message author can flip pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user to interact with the buttons
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        """Edit the original message to show the new page."""
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

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
        if self.index < len(self.embeds) - 1:
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
        Fetch the content of a crash-report webpage and unify:
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

                # Extract sections via regex
                exception_match = re.search(r"\+ Exception\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)
                enhanced_stacktrace_match = re.search(r"\+ Enhanced Stacktrace\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)
                installed_modules_match = re.search(r"\+ Installed Modules\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)

                exception_text = exception_match.group(1).strip() if exception_match else None
                stacktrace_text = enhanced_stacktrace_match.group(1).strip() if enhanced_stacktrace_match else None

                # Capture installed modules, just the mod names
                mod_names = []
                if installed_modules_match:
                    installed_modules_raw = installed_modules_match.group(1).strip()
                    for line in installed_modules_raw.splitlines():
                        line = line.strip()
                        if not line.startswith("+ "):
                            continue
                        # Capture everything after '+ ' until '(' or end of line
                        m = re.match(r"\+\s+(.+?)(?:\(|$)", line)
                        if m:
                            mod_names.append(m.group(1).strip())
                # Convert the mod names list to a single string, or None if none found
                installed_modules_text = "\n".join(mod_names) if mod_names else None

                return {
                    "full_text": full_text.strip(),  # Entire text if needed
                    "exception": exception_text,
                    "enhanced_stacktrace": stacktrace_text,
                    "installed_modules": installed_modules_text
                }
        except Exception as e:
            return {"error": f"Error fetching webpage: {e}"}

    def build_unified_pages(self, data: dict, link: str) -> list[discord.Embed]:
        """
        Combine Exception, Enhanced Stacktrace, and Installed Modules into one big string,
        then chunk it across multiple embed pages with code block formatting.
        """
        # Build a single text block with all relevant sections in order
        sections = []
        sections.append(f"Crash report content extracted from the link: {link}\n")

        if data.get("exception"):
            sections.append("## Exception\n" + data["exception"])

        if data.get("enhanced_stacktrace"):
            sections.append("## Enhanced Stacktrace\n" + data["enhanced_stacktrace"])

        if data.get("installed_modules"):
            sections.append("## User's Modlist\n" + data["installed_modules"])

        # If absolutely nothing found, fallback
        if len(sections) == 1 and sections[0].startswith("Crash report content extracted"):
            sections.append("Nothing found. Possibly invalid or missing data.")

        unified_text = "\n\n".join(sections).strip()
        if not unified_text:
            unified_text = "No data to display."

        # Now we chunk this text into multiple pages
        MAX_EMBED_FIELD_LENGTH = 1024
        # We'll reserve ~6-10 chars for code-block formatting & newlines, let's do 10
        # to be safe for line breaks
        CODEBLOCK_OVERHEAD = 10
        chunk_size = MAX_EMBED_FIELD_LENGTH - CODEBLOCK_OVERHEAD

        pages = []
        start_idx = 0
        while start_idx < len(unified_text):
            # We'll grab a chunk of up to chunk_size
            end_idx = min(start_idx + chunk_size, len(unified_text))
            chunk = unified_text[start_idx:end_idx]
            pages.append(chunk)
            start_idx += chunk_size

        # Build the embed(s)
        embeds = []
        total_pages = len(pages)
        for i, page_text in enumerate(pages, start=1):
            embed = discord.Embed(
                title=f"Crash Report (Page {i}/{total_pages})",
                color=discord.Color.red()
            )
            # We put the chunk in code blocks
            embed.add_field(name="Details", value=f"```{page_text}```", inline=False)
            embeds.append(embed)

        return embeds

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Command to analyze a media or webpage URL.
        For crash reports: Unify all sections in one multi-page embed with buttons.
        """
        # Check if it's a crash report URL
        if "report.butr.link" in url:
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            # Build the unified pages
            pages = self.build_unified_pages(data, link=url)
            if not pages:
                return await ctx.send("No crash report content found.")

            # If only 1 page, just send a normal embed (no need for buttons)
            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            # Otherwise, send with pagination
            view = PaginatedEmbeds(pages, invoker_id=ctx.author.id)
            await ctx.send(embed=pages[0], view=view)

        else:
            # Handle image analysis instead
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
        Automatic handling of crash-report links or attachments in messages.
        Same logic: unify everything into one set of pages.
        """
        if message.author.bot:
            return

        # Look for crash-report links
        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url:
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                # Build unified pages
                pages = self.build_unified_pages(data, link=url)
                if not pages:
                    await message.reply("No crash report content found.")
                    return

                # 1 page or multiple?
                if len(pages) == 1:
                    await message.reply(embed=pages[0])
                else:
                    view = PaginatedEmbeds(pages, invoker_id=message.author.id)
                    await message.reply(embed=pages[0], view=view)
                return

        # If not a crash-report link, check attachments for images/html
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
                        # We could do the same chunking/pagination for HTML,
                        # but here's a simple approach:
                        embed = discord.Embed(
                            title="Crash Report Analysis (HTML)",
                            description="Extracted text from the uploaded HTML crash report.",
                            color=discord.Color.blue(),
                        )
                        truncated = text[:1024]  # Just for safety
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
