import discord
from discord.ui import View, Button
from redbot.core import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re

def extract_section(full_text: str, section_name: str, other_sections: list[str]) -> str:
    """
    Extract everything from `section_name` until we hit `other_sections`
    or the end of the file, ignoring lines like '+ IL:'.
    The section header can be prefixed with either '+' or '-'.

    Args:
        full_text (str): The complete text to search within.
        section_name (str): The name of the section to extract.
        other_sections (list[str]): Names of other sections to stop at.

    Returns:
        str: The extracted section content or an empty string if not found.
    """
    # If no "other sections" were provided, we might just stop at the end (\Z)
    if not other_sections:
        other_sections = []
    # Allow for '+' or '-' prefixes
    prefix_pattern = r"[+-]"
    # Escape section names to handle any regex special characters
    escaped_section = re.escape(section_name)
    escaped_other_sections = '|'.join(re.escape(sec) for sec in other_sections)
    pattern = rf"(?s){prefix_pattern}\s+{escaped_section}\s*(.*?)\s*(?={prefix_pattern}\s+(?:{escaped_other_sections})|\Z)"
    match = re.search(pattern, full_text)
    return match.group(1).strip() if match else ""

class PaginatedEmbeds(View):
    """
    A single View-based paginator that cycles through 'pages',
    where each page is a list of embed objects (so we can send multiple embeds in one message).
    """
    def __init__(self, pages: list[list[discord.Embed]], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.pages = pages
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the original user can press the buttons
        return interaction.user.id == self.invoker_id

    async def update_message(self, interaction: discord.Interaction):
        # Edits the existing message to show the new set of embeds
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
        in a way that doesn't break on '+ IL:' lines or short-circuit after 1 mod.

        Args:
            url (str): The URL of the crash report.

        Returns:
            dict: A dictionary containing extracted sections or an error message.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                full_text = BeautifulSoup(html_content, "html.parser").get_text()

                # Define the sections that might appear
                sections = ["Exception", "Enhanced Stacktrace", "Installed Modules"]
                # Individually extract each section by name

                exception_text = extract_section(
                    full_text,
                    "Exception",
                    other_sections=["Enhanced Stacktrace", "Installed Modules"]
                )
                stacktrace_text = extract_section(
                    full_text,
                    "Enhanced Stacktrace",
                    other_sections=["Exception", "Installed Modules"]
                )
                modules_text = extract_section(
                    full_text,
                    "Installed Modules",
                    other_sections=["Exception", "Enhanced Stacktrace"]
                )

                # Parse out the mod lines from modules_text
                # Lines like: "+ Harmony (Bannerlord.Harmony, v2.3.3.207)"
                mods_found = []
                for line in modules_text.splitlines():
                    line = line.strip()
                    if line.startswith("+ ") or line.startswith("- "):
                        # Capture everything after '+ ' or '- ' up until '(' or end
                        m = re.match(r"[+-]\s+(.*?)(?:\s*\(|$)", line)
                        if m:
                            name = m.group(1).strip()
                            if name:
                                mods_found.append(name)
                # Convert them back to a single string for display
                installed_modules = "\n".join(mods_found)

                return {
                    "exception": exception_text,
                    "enhanced_stacktrace": stacktrace_text,
                    "installed_modules": installed_modules
                }

        except Exception as e:
            return {"error": f"Error fetching webpage: {e}"}

    async def analyze_media(self, image_data: bytes) -> dict:
        """Analyze image bytes with pytesseract.

        Args:
            image_data (bytes): The image data to analyze.

        Returns:
            dict: A dictionary containing extracted text and image resolution or an error message.
        """
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
        Break up the content for one section into multiple embeds (if necessary).
        - The first chunk has a title; subsequent chunks are blank so it reads seamlessly.

        Args:
            section_title (str): The title of the section.
            content (str): The content of the section.

        Returns:
            list[discord.Embed]: A list of Discord embed objects.
        """
        content = content.strip()
        if not content:
            return []

        CHUNK_SIZE = 1024 - 10  # leave space for code blocks, newlines
        # Replace multiple consecutive newlines with a single newline for better formatting
        content = re.sub(r'\n+', '\n', content)
        # We'll do a simple character-based chunking
        chunks = [content[i:i + CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE)]

        embeds = []
        for i, chunk_text in enumerate(chunks, start=1):
            # Only the first chunk has the section title
            embed_title = section_title if i == 1 else ""
            embed = discord.Embed(title=embed_title, color=discord.Color.blue())

            # We'll put the chunk in a code block for readability
            embed.add_field(
                name="",  # no header => seamless
                value=f"```\n{chunk_text}\n```",
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
        Build the final multi-page structure:
          Page 1 => all Exception embeds
          Page 2 => all Enhanced Stacktrace embeds
          Page 3 => all Installed Modules embeds
        If a section is empty, skip it.
        If everything is empty, return a single page that says "No Crash Report Data Found."

        Args:
            exception_text (str): The extracted Exception section.
            stacktrace_text (str): The extracted Enhanced Stacktrace section.
            installed_modules_text (str): The extracted Installed Modules section.

        Returns:
            list[list[discord.Embed]]: A list of pages, each containing a list of embeds.
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

        if not pages:
            no_data_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[no_data_embed]]

        return pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Command to analyze a crash report or an image link.
        - Crash report: multi-page single message, each page has 1+ embeds for a section
        - Image link: do OCR

        Usage:
            !analyze <url>
        """
        # If it's a crash report link
        if "report.butr.link" in url:
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            # Unpack
            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            mods_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, mods_text)

            if len(pages) == 1:
                # Only one "page" => might have multiple embeds
                return await ctx.send(embeds=pages[0])

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
                    else:
                        embed = discord.Embed(
                            title="Media Analysis",
                            description=f"Content extracted from the media at {url}",
                            color=discord.Color.green(),
                        )
                        embed.add_field(
                            name="Text Content",
                            value=f"```\n{analysis['text'] or 'No text found'}\n```",
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
        """Auto-detect crash-report links in normal messages."""
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
                    await message.reply(embeds=pages[0])
                else:
                    view = PaginatedEmbeds(pages, message.author.id)
                    await message.reply(embeds=pages[0], view=view)

                return

        # If no crash-report link, you could still handle attachments here if needed...


async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
