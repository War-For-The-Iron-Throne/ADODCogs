import discord
from redbot.core import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re


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

                # Extract "Exception" and "Enhanced Stacktrace" sections
                exception_match = re.search(r"\+ Exception\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)
                enhanced_stacktrace_match = re.search(r"\+ Enhanced Stacktrace\n(.+?)(?=\n\n|\Z)", full_text, re.DOTALL)

                return {
                    "full_text": full_text.strip(),
                    "exception": exception_match.group(1).strip() if exception_match else "Not Found",
                    "enhanced_stacktrace": enhanced_stacktrace_match.group(1).strip() if enhanced_stacktrace_match else "Not Found",
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

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """Command to analyze a media or webpage URL."""
        if "report.butr.link" in url:
            # Handle crash report webpage
            data = await self.fetch_webpage(url)
            if "error" in data:
                await ctx.send(data["error"])
                return

            embed = discord.Embed(
                title="Crash Report Analysis",
                description=f"Crash report content extracted from [the link]({url}):",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="Exception",
                value=f"```{data['exception'][:1018]}```" if data["exception"] else "Not Found",
                inline=False,
            )
            embed.add_field(
                name="Enhanced Stacktrace",
                value=f"```{data['enhanced_stacktrace'][:1018]}```" if data["enhanced_stacktrace"] else "Not Found",
                inline=False,
            )
            await ctx.send(embed=embed)
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

        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url:
                # Handle crash report URL
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                embed = discord.Embed(
                    title="Crash Report Analysis",
                    description=f"Crash report content extracted from [the link]({url}):",
                    color=discord.Color.red(),
                )
                embed.add_field(
                    name="Exception",
                    value=f"```{data['exception'][:1018]}```" if data["exception"] else "Not Found",
                    inline=False,
                )
                embed.add_field(
                    name="Enhanced Stacktrace",
                    value=f"```{data['enhanced_stacktrace'][:1018]}```" if data["enhanced_stacktrace"] else "Not Found",
                    inline=False,
                )
                await message.reply(embed=embed)
                return

        # Handle attachments as images or HTML files
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
