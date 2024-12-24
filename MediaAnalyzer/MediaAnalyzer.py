import discord
from discord.ext import commands
from PIL import Image
import pytesseract
from io import BytesIO
import aiohttp


class MediaAnalyzer(commands.Cog):
    """Analyze images and GIFs for AI Assistant functionality."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_load(self) -> None:
        """Executed when the cog is loaded."""
        print("MediaAnalyzer cog has been loaded successfully.")

    async def cog_unload(self) -> None:
        """Executed when the cog is unloaded."""
        await self.session.close()
        print("MediaAnalyzer cog has been unloaded and resources cleaned up.")

    async def analyze_media(self, url: str, *args, **kwargs) -> dict:
        """Analyzes media from a given URL for text and content."""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": "Failed to fetch the media."}

                content_type = response.headers.get("Content-Type", "").lower()
                if "image" not in content_type:
                    return {"error": "The provided URL does not point to an image."}

                image_data = BytesIO(await response.read())

            # Analyze the image
            image = Image.open(image_data)
            text = pytesseract.image_to_string(image)

            return {
                "url": url,
                "text": text.strip(),
                "width": image.width,
                "height": image.height,
            }
        except Exception as e:
            return {"error": str(e)}

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """Command to analyze a media URL."""
        analysis = await self.analyze_media(url)
        if "error" in analysis:
            await ctx.send(f"Error: {analysis['error']}")
            return

        embed = discord.Embed(
            title="Media Analysis",
            description=f"Content extracted from the media at {url}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Text Content", value=analysis["text"] or "No text found", inline=False)
        embed.add_field(name="Resolution", value=f"{analysis['width']}x{analysis['height']}", inline=False)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_assistant_cog_add(self, cog: commands.Cog):
        """Register the analyze_media function with Assistant."""
        schema = {
            "name": "analyze_media",
            "description": "Analyze an image or GIF URL for content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the media to analyze.",
                    }
                },
                "required": ["url"],
            },
        }
        await cog.register_function(cog_name="MediaAnalyzer", schema=schema)


async def setup(bot: commands.Bot):
    await bot.add_cog(MediaAnalyzer(bot))
