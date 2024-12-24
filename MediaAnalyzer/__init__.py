from redbot.core.bot import Red
from .MediaAnalyzer import MediaAnalyzer

async def setup(bot: Red):
    cog = MediaAnalyzer(bot)
    await bot.add_cog(cog)
