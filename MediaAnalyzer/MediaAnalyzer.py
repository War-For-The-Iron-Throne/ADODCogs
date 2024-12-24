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
    A simple View that handles 'Previous' and 'Next' button presses to flip through a list of embeds.
    """
    def __init__(self, embeds, user_id: int):
        super().__init__(timeout=180)  # 3-minute timeout, for example
        self.embeds = embeds
        self.index = 0
        self.user_id = user_id  # So only the command invoker can flip pages

    async def update_message(self, interaction: discord.Interaction):
        # Update the embed in-place
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        # Ensure only the original user can flip pages
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "You can't control this paginator.", ephemeral=True
            )

        if self.index > 0:
            self.index -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.send_message("You're already on the first page!", ephemeral=True)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "You can't control this paginator.", ephemeral=True
            )

        if self.index < len(self.embeds) - 1:
            self.index += 1
            await self.update_message(interaction)
        else:
            await interaction.response.send_message("You're already on the last page!", ephemeral=True)


class MediaAnalyzer(commands.Cog):
    """Analyze images, crash reports, and webpages for AI Assistant functionality."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_load(self) -> None:
        """Executed when the cog is loaded."""
        print("MediaAnalyzer cog has been loaded successfully.")

    async def cog_unload(self) -> None:
        """Executed when the cog is unloaded."""
        if self.session:
            await self.session.close()
        print("MediaAnalyzer cog has been unloaded and resources cleaned up.")

    async def fetch_webpage(self, url: str) -> dict:
        """Fetch the content of a crash report webpage and extract relevant sections."""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}
                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                # Extract specific sections via regex
                exception_match = re.search(r"\+ Exception\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)
                enhanced_stacktrace_match = re.search(r"\+ Enhanced Stacktrace\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)
                installed_modules_match = re.search(r"\+ Installed Modules\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)

                # If installed modules are found, parse out only the 'names'
                if installed_modules_match:
                    installed_modules_raw = installed_modules_match.group(1).strip()

                    # Example lines:
                    #   + Harmony (Bannerlord.Harmony, v2.3.3.207)
                    #   + A Dance of Dragons - Code (ADODCODE, v1.2.11.0)
                    # We'll grab everything between '+ ' and '(' or the end of line
                    mod_lines = installed_modules_raw.splitlines()
                    just_names = []
                    for line in mod_lines:
                        line = line.strip()
                        if not line.startswith("+ "):
                            continue
                        # This pattern picks up everything after '+ ' up until '(' or end of string
                        match = re.match(r"\+\s+(.+?)(?:\(|$)", line)
                        if match:
                            just_names.append(match.group(1).strip())

                    installed_modules_clean = "\n".join(just_names) if just_names else "Not Found"
                else:
                    installed_modules_clean = "Not Found"

                return {
                    "full_text": full_text.strip(),
                    "exception": exception_match.group(1).strip() if exception_match else "Not Found",
                    "enhanced_stacktrace": enhanced_stacktrace_match.group(1).strip() if enhanced_stacktrace_match else "Not Found",
                    "installed_modules": installed_modules_clean,
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

    def build_embeds_with_buttons(
        self, title: str, description: str, content: str
    ) -> list[discord.Embed]:
        """
        Build a list of Embeds, each containing a chunk of content, which can be
        paginated with Next/Prev buttons. We aim to include as much as possible
        in each embed while respecting the 1024-char limit in a single field.
        """
        MAX_EMBED_FIELD_LENGTH = 1024
        # Reserve space for code block formatting (6 chars: ```\n and \n```)
        # plus a little buffer for newlines, etc.
        CHARS_FOR_CODEBLOCK = 6
        MAX_CONTENT_LENGTH = MAX_EMBED_FIELD_LENGTH - CHARS_FOR_CODEBLOCK

        lines = content.split("\n")

        # We'll build one or more fields. We combine lines until we exceed the chunk limit.
        pages = []
        current_chunk = ""
        for line in lines:
            # +1 for the newline we might add
            if len(current_chunk) + len(line) + 1 <= MAX_CONTENT_LENGTH:
                current_chunk += line + "\n"
            else:
                pages.append(current_chunk.rstrip("\n"))
                current_chunk = line + "\n"

        # Append the last chunk if it exists
        if current_chunk.strip():
            pages.append(current_chunk.rstrip("\n"))

        # Now build each embed
        embeds = []
        for idx, chunk in enumerate(pages):
            embed = discord.Embed(
                title=f"{title} (Page {idx + 1}/{len(pages)})",
                description=description if idx == 0 else None,
                color=discord.Color.red(),
            )
            embed.add_field(name="Details", value=f"```{chunk}```", inline=False)
            embeds.append(embed)

        return embeds

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """Command to analyze a media or webpage URL."""
        if "report.butr.link" in url:
            # Handle crash report webpage
            data = await self.fetch_webpage(url)
            if "error" in data:
                await ctx.send(data["error"])
                return

            description = f"Crash report content extracted from [the link]({url}):"

            # Build up to three different sets of pages:
            #   1) Exception
            #   2) Enhanced Stacktrace
            #   3) Installed Modules
            # We'll unify them if you prefer, but let's do them individually for clarity
            # and send them with button-based pagination for each section.

            # 1) Exception
            if data["exception"] != "Not Found":
                exc_embeds = self.build_embeds_with_buttons(
                    "Exception", description, data["exception"]
                )
                if exc_embeds:
                    view = PaginatedEmbeds(exc_embeds, user_id=ctx.author.id)
                    await ctx.send(embed=exc_embeds[0], view=view)

            # 2) Enhanced Stacktrace
            if data["enhanced_stacktrace"] != "Not Found":
                stack_embeds = self.build_embeds_with_buttons(
                    "Enhanced Stacktrace", description, data["enhanced_stacktrace"]
                )
                if stack_embeds:
                    view = PaginatedEmbeds(stack_embeds, user_id=ctx.author.id)
                    await ctx.send(embed=stack_embeds[0], view=view)

            # 3) Installed Modules
            if data["installed_modules"] != "Not Found":
                modlist_embeds = self.build_embeds_with_buttons(
                    "User's Modlist", description, data["installed_modules"]
                )
                if modlist_embeds:
                    view = PaginatedEmbeds(modlist_embeds, user_id=ctx.author.id)
                    await ctx.send(embed=modlist_embeds[0], view=view)

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
        """Handle uploaded images, HTML files, and crash report URLs."""
        if message.author.bot:
            return

        # Look for URLs in the message content
        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url:
                # Handle crash report URL
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                description = f"Crash report content extracted from [the link]({url}):"

                # 1) Exception
                if data["exception"] != "Not Found":
                    exc_embeds = self.build_embeds_with_buttons(
                        "Exception", description, data["exception"]
                    )
                    if exc_embeds:
                        view = PaginatedEmbeds(exc_embeds, user_id=message.author.id)
                        await message.reply(embed=exc_embeds[0], view=view)

                # 2) Enhanced Stacktrace
                if data["enhanced_stacktrace"] != "Not Found":
                    stack_embeds = self.build_embeds_with_buttons(
                        "Enhanced Stacktrace", description, data["enhanced_stacktrace"]
                    )
                    if stack_embeds:
                        view = PaginatedEmbeds(stack_embeds, user_id=message.author.id)
                        await message.reply(embed=stack_embeds[0], view=view)

                # 3) Installed Modules
                if data["installed_modules"] != "Not Found":
                    modlist_embeds = self.build_embeds_with_buttons(
                        "User's Modlist", description, data["installed_modules"]
                    )
                    if modlist_embeds:
                        view = PaginatedEmbeds(modlist_embeds, user_id=message.author.id)
                        await message.reply(embed=modlist_embeds[0], view=view)

                return

        # Look for attachments in the message
        if message.attachments:
            for attachment in message.attachments:
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
                elif attachment.filename.lower().endswith('.html'):
                    try:
                        html_bytes = await attachment.read()
                        html_content = html_bytes.decode('utf-8')
                        soup = BeautifulSoup(html_content, 'html.parser')
                        text = soup.get_text()
                        embed = discord.Embed(
                            title="Crash Report Analysis",
                            description="Extracted text from the uploaded HTML crash report.",
                            color=discord.Color.blue(),
                        )
                        # Truncate if needed to avoid any embed overflow
                        embed.add_field(name="Extracted Content", value=text[:1024], inline=False)
                        await message.reply(embed=embed)
                    except Exception as e:
                        await message.reply(f"Error processing HTML file: {e}")


async def setup(bot):
    """Proper async setup for the cog."""
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
