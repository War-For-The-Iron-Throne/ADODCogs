import discord
from discord.ext import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re


class MediaAnalyzer(commands.Cog):
    """Analyze images and crash reports for AI Assistant functionality."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_load(self) -> None:
        print("MediaAnalyzer cog has been loaded successfully.")

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()
        print("MediaAnalyzer cog has been unloaded and resources cleaned up.")

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

    def extract_text_from_html(self, html_content: str) -> str:
        """Extract text from HTML content."""
        soup = BeautifulSoup(html_content, 'html.parser')
        return soup.get_text()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle uploaded images and crash reports."""
        if message.author.bot:
            return

        # Analyze attached images
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
                        text = self.extract_text_from_html(html_content)
                        # You can add further processing for crash reports here if needed
                        embed = discord.Embed(
                            title="Crash Report Analysis",
                            description="Extracted text from the uploaded HTML crash report.",
                            color=discord.Color.blue(),
                        )
                        embed.add_field(name="Extracted Content", value=text[:1024], inline=False)
                        await message.reply(embed=embed)
                    except Exception as e:
                        await message.reply(f"Error processing HTML file: {e}")

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """Command to analyze a media URL."""
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


async def setup(bot):
    cog = MediaAnalyzer(bot)
    await bot.add_cog(cog)
