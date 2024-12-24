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
    """A single View-based paginator that cycles through one continuous list of pages (embeds)."""

    def __init__(self, pages: list[discord.Embed], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.pages = pages
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """
        Only allow the original user to interact with pagination,
        so random people can’t hijack it.
        """
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        """Edits the existing message to show the new page."""
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

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

                # Regex matches
                exception_match = re.search(
                    r"\+ Exception\s+(.+?)(?=\n\n|\Z)",
                    full_text,
                    re.DOTALL
                )
                stacktrace_match = re.search(
                    r"\+ Enhanced Stacktrace\s+(.+?)(?=\n\n|\Z)",
                    full_text,
                    re.DOTALL
                )
                modules_match = re.search(
                    r"\+ Installed Modules\s+(.+?)(?=\n\n|\Z)",
                    full_text,
                    re.DOTALL
                )

                # Prepare each data piece (None if not found)
                exception_text = (
                    exception_match.group(1).strip() if exception_match else None
                )
                stacktrace_text = (
                    stacktrace_match.group(1).strip() if stacktrace_match else None
                )

                # Installed modules: parse lines, extracting only mod name
                installed_modules_text = None
                if modules_match:
                    mod_raw = modules_match.group(1).strip()
                    mod_names = []
                    for line in mod_raw.splitlines():
                        line = line.strip()
                        if not line.startswith("+ "):
                            continue
                        # capture text after '+ ' up to '(' or EOL
                        m = re.match(r"\+\s+(.+?)(?:\(|$)", line)
                        if m:
                            mod_names.append(m.group(1).strip())

                    if mod_names:
                        installed_modules_text = "\n".join(mod_names)

                return {
                    "exception": exception_text,
                    "enhanced_stacktrace": stacktrace_text,
                    "installed_modules": installed_modules_text,
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

    def build_section_pages(self, section_name: str, content: str) -> list[discord.Embed]:
        """
        Given a single section (e.g., 'Exception') and its content,
        chunk it into multiple pages if necessary, returning a list of embeds.
        Each embed has a single field named "Details".
        """
        # If there's no content, return an empty list
        if not content.strip():
            return []

        # We'll chunk the content based on the max field limit
        MAX_EMBED_FIELD_LENGTH = 1024
        # Subtract ~10 characters for code blocks/newlines, etc.
        chunk_size = MAX_EMBED_FIELD_LENGTH - 10

        # Split into pages
        chunks = []
        start_idx = 0
        while start_idx < len(content):
            end_idx = min(start_idx + chunk_size, len(content))
            chunks.append(content[start_idx:end_idx])
            start_idx += chunk_size

        # Build an embed for each chunk
        pages = []
        total = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title=f"{section_name} (Page {i}/{total})",
                color=discord.Color.red(),
            )
            embed.add_field(name="Details", value=f"```{chunk}```", inline=False)
            pages.append(embed)

        return pages

    def build_all_pages_unified(
        self,
        exception_text: str | None,
        stacktrace_text: str | None,
        installed_modules_text: str | None,
        link: str
    ) -> list[discord.Embed]:
        """
        Build a single list of pages that covers:
          1) Exception (split across pages if needed)
          2) Enhanced Stacktrace
          3) User's Modlist
        Each chunk gets its own embed, but they're unified under a single paginator.
        """
        all_pages = []

        # If absolutely nothing is found, produce a single "no data" page
        if not any([exception_text, stacktrace_text, installed_modules_text]):
            embed = discord.Embed(
                title="No Crash Report Data Found",
                description=f"Nothing extracted from {link}",
                color=discord.Color.red(),
            )
            return [embed]

        # 1) Exception
        if exception_text:
            exc_pages = self.build_section_pages("Exception", exception_text)
            all_pages.extend(exc_pages)

        # 2) Enhanced Stacktrace
        if stacktrace_text:
            stack_pages = self.build_section_pages("Enhanced Stacktrace", stacktrace_text)
            all_pages.extend(stack_pages)

        # 3) Installed Modules
        if installed_modules_text:
            modlist_pages = self.build_section_pages("User's Modlist", installed_modules_text)
            all_pages.extend(modlist_pages)

        # If we actually built no pages for some reason, show fallback
        if not all_pages:
            embed = discord.Embed(
                title="No Crash Report Data Found",
                description=f"Nothing extracted from {link}",
                color=discord.Color.red(),
            )
            all_pages.append(embed)

        return all_pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Analyze a crash report or media. For crash reports:
        - Single unified paginator
        - Pages for Exception, Stacktrace, Modlist
        """
        if "report.butr.link" in url:
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            # Build a single list of pages
            pages = self.build_all_pages_unified(
                exception_text=data.get("exception") or "",
                stacktrace_text=data.get("enhanced_stacktrace") or "",
                installed_modules_text=data.get("installed_modules") or "",
                link=url
            )

            # If only 1 page, send without buttons
            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            # Otherwise, set up the button-based pagination
            view = PaginatedEmbeds(pages, ctx.author.id)
            await ctx.send(embed=pages[0], view=view)

        else:
            # Handle image analysis
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
        """Auto-detect crash report links or attachments in messages."""
        if message.author.bot:
            return

        # 1) Detect crash-report links
        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url:
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                pages = self.build_all_pages_unified(
                    exception_text=data.get("exception") or "",
                    stacktrace_text=data.get("enhanced_stacktrace") or "",
                    installed_modules_text=data.get("installed_modules") or "",
                    link=url
                )

                if len(pages) == 1:
                    await message.reply(embed=pages[0])
                else:
                    view = PaginatedEmbeds(pages, message.author.id)
                    await message.reply(embed=pages[0], view=view)

                return  # stop after handling the link

        # 2) If no crash-report link, check attachments
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

                # HTML crash-report file (optional)
                elif attachment.filename.lower().endswith('.html'):
                    try:
                        html_bytes = await attachment.read()
                        html_content = html_bytes.decode('utf-8')
                        soup = BeautifulSoup(html_content, 'html.parser')
                        text = soup.get_text()
                        embed = discord.Embed(
                            title="Crash Report Analysis (HTML)",
                            description="Extracted text from the uploaded HTML crash report.",
                            color=discord.Color.blue(),
                        )
                        # Truncate if necessary
                        truncated = text[:1024]
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
