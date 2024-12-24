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
          - Installed Modules
        using regex that captures everything up until the next section header or the end of the file.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                full_text = BeautifulSoup(html_content, "html.parser").get_text()

                # Update regex patterns to match '-' instead of '+'
                # Also make the section header recognition more flexible
                # 1) Exception: capture from '- Exception' up to the next line that starts with '- ' or '+ ' or EOF
                exception_regex = re.compile(
                    r"[-+] Exception\s+([\s\S]+?)(?=\n[-+]\s|\Z)"
                )
                exception_match = exception_regex.search(full_text)
                exception_text = exception_match.group(1).strip() if exception_match else ""

                # 2) Enhanced Stacktrace
                stacktrace_regex = re.compile(
                    r"[-+] Enhanced Stacktrace\s+([\s\S]+?)(?=\n[-+]\s|\Z)"
                )
                stacktrace_match = stacktrace_regex.search(full_text)
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""

                # 3) Installed Modules: capture from '- Installed Modules' up to next '- ' or '+ ' or EOF
                modules_regex = re.compile(
                    r"[-+] Installed Modules\s+([\s\S]+?)(?=\n[-+]\s|\Z)"
                )
                modules_match = modules_regex.search(full_text)
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    # More robust findall to get ALL lines that start with '+' or '-'
                    # e.g. '- Harmony (Bannerlord.Harmony, v2.3.3.207)'
                    # We'll capture everything after '+ ' or '- ' up to '(' or end-of-line
                    mods = re.findall(r"^[+-]\s+(.*?)(?:\(|$)", modules_block, re.MULTILINE)
                    # Clean them up
                    mod_names = [m.strip() for m in mods if m.strip()]
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

        CHUNK_SIZE = 1024 - 10  # 10 chars overhead for code block formatting, newlines, etc.
        chunks = []
        start_idx = 0
        while start_idx < len(content):
            end_idx = min(start_idx + CHUNK_SIZE, len(content))
            chunk = content[start_idx:end_idx]
            chunks.append(chunk)
            start_idx += CHUNK_SIZE

        embeds = []
        total_chunks = len(chunks)

        for i, chunk_text in enumerate(chunks, start=1):
            # For the first chunk of this section, show the section_name in the embed title
            # For subsequent chunks, omit the title (or set it to "", if you prefer).
            if i == 1:
                embed_title = section_name
            else:
                embed_title = ""  # or None, but empty string = no visible title

            embed = discord.Embed(
                title=embed_title,
                color=discord.Color.blue()
            )
            # Add the code-block chunk
            embed.add_field(
                name="",  # no field name so it's "seamless"
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
        """
        # If it's a crash report
        if "report.butr.link" in url:
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            mods_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, mods_text)
            # Each element in 'pages' is a list of embed objects

            if len(pages) == 1:
                # Only one "page," so no need for next/prev
                # But that page might have multiple embeds, so we can do:
                return await ctx.send(embeds=pages[0])

            # Otherwise, use the paginator
            view = PaginatedEmbeds(pages, ctx.author.id)
            await ctx.send(embeds=pages[0], view=view)

        else:
            # Otherwise, handle image analysis
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
        """Auto-detect crash report links in messages."""
        if message.author.bot:
            return

        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url:
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

        # If no crash-report link, you could still handle attachments here, if desired
        # (omitted for brevity)

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
