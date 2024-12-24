from .mediaanalyzer import MediaAnalyzer

def setup(bot):
    bot.add_cog(MediaAnalyzer(bot))
