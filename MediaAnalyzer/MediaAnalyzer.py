import discord
from discord.ext import commands
import aiohttp
import re
from bs4 import BeautifulSoup
from PIL import Image
import pytesseract
from io import BytesIO
import json
import traceback

###############################################################################
# Crash-Report / Image Parsing Cog (No Embeds)
###############################################################################

class MediaAnalyzerAssistant(commands.Cog):
    """
    A cog that integrates with the 'Assistant' Cog by registering
    a function to parse crash reports (and optionally images).
    No giant embeds, purely function-based.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        # Cleanup
        if self.session:
            await self.session.close()

    ###########################################################################
    # MAIN PARSING LOGIC
    ###########################################################################

    async def fetch_webpage(self, url: str) -> dict:
        """
        Fetch a crash-report webpage and parse out:
          - Exception
          - Enhanced Stacktrace
          - Installed Modules
        Returns a dict or {"error": "..."} upon failure.
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"HTTP {response.status} while fetching {url}."}

                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                # Define pattern of headings to stop at:
                headings_pattern = (
                    r"(?:Exception|Enhanced Stacktrace|Installed Modules|"
                    r"Loaded BLSE Plugins|Involved Modules and Plugins|Assemblies|"
                    r"Native Assemblies|Harmony Patches|Log Files|Mini Dump|Save File|"
                    r"Screenshot|Screenshot Data|Json Model Data)"
                )

                # Regex for each major section:
                exception_regex = re.compile(
                    rf"[+-]\s*Exception\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )
                stacktrace_regex = re.compile(
                    rf"[+-]\s*Enhanced Stacktrace\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )
                modules_regex = re.compile(
                    rf"[+-]\s*Installed Modules\s+([\s\S]+?)(?=\n[+-]\s*{headings_pattern}|\Z)",
                    re.IGNORECASE
                )

                exception_match = exception_regex.search(full_text)
                stacktrace_match = stacktrace_regex.search(full_text)
                modules_match = modules_regex.search(full_text)

                exception_text = (exception_match.group(1).strip()
                                  if exception_match else "")
                stacktrace_text = (stacktrace_match.group(1).strip()
                                   if stacktrace_match else "")
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    mod_lines = re.findall(
                        r"^[+-]\s+(.*?)(?:\(|$)",
                        modules_block,
                        re.MULTILINE
                    )
                    mod_names = [m.strip() for m in mod_lines if m.strip()]
                    if mod_names:
                        installed_modules_text = "\n".join(mod_names)

                return {
                    "exception": exception_text,
                    "stacktrace": stacktrace_text,
                    "modules": installed_modules_text
                }
        except Exception as exc:
            return {"error": f"Error: {exc}\nTraceback:\n{traceback.format_exc()}"}

    async def parse_crash_report_summary(self, url: str, *args, **kwargs) -> str:
        """
        This function is what Assistant will call.
        We'll fetch the link, parse it, and return a short summary
        as a normal string.
        """
        data = await self.fetch_webpage(url)
        if "error" in data:
            return f"ERROR parsing crash report: {data['error']}"

        # Summarize
        lines = []
        if data["exception"]:
            lines.append(f"**Exception**:\n{data['exception'][:500]}")  # limit to 500 chars
        if data["stacktrace"]:
            lines.append(f"**Stacktrace** (partial):\n{data['stacktrace'][:500]}")
        if data["modules"]:
            # Potentially large, so let's only show first ~20 lines
            modlist = data["modules"].split("\n")
            short_modlist = modlist[:20]
            lines.append("**Installed Modules (partial)**:\n" + "\n".join(short_modlist))
            if len(modlist) > 20:
                lines.append(f"... (and {len(modlist)-20} more modules)")

        if not lines:
            return "No crash report data found (exception, stacktrace, or modules)."

        summary = "\n\n".join(lines)
        return summary

    ###########################################################################
    # OPTIONAL: If you want to analyze images with the assistant
    ###########################################################################

    async def analyze_image_summary(self, url: str, *args, **kwargs) -> str:
        """
        Example: fetch an image, OCR it, return a short summary.
        """
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return f"Failed to fetch image. HTTP {resp.status}."
                image_data = await resp.read()

            image = Image.open(BytesIO(image_data))
            text = pytesseract.image_to_string(image) or ""
            text_summary = text.strip()[:400]  # limit to 400 chars
            return (
                f"Image resolution: {image.width}x{image.height}\n"
                f"OCR (partial): {text_summary}"
            )

        except Exception as exc:
            return f"Error analyzing image: {exc}\n{traceback.format_exc()}"

    ###########################################################################
    # REGISTERING THE FUNCTIONS WITH ASSISTANT
    ###########################################################################

    @commands.Cog.listener()
    async def on_assistant_cog_add(self, assistant_cog: commands.Cog):
        """
        Called automatically when the Assistant cog is loaded or reloaded.
        We then register our functions with the Assistant so GPT can call them.
        """
        # Build a JSON schema for parse_crash_report_summary
        crash_report_schema = {
            "name": "parse_crash_report_summary",
            "description": (
                "Fetches a BUTR crash report URL and returns a summarized "
                "exception, stacktrace, and modules (if found)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to the crash report.",
                    }
                },
                "required": ["url"],
            },
        }

        # Build a JSON schema for analyze_image_summary (if you want it)
        image_schema = {
            "name": "analyze_image_summary",
            "description": "Fetch an image by URL and return a short OCR summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The image URL to analyze."
                    }
                },
                "required": ["url"],
            },
        }

        # Register them with the Assistant
        # If you have multiple, do them in one call with register_functions, or individually.
        await assistant_cog.register_functions(
            cog_name="MediaAnalyzerAssistant",
            schemas=[crash_report_schema, image_schema]
        )

    ###########################################################################
    # OPTIONAL: MANUAL COMMANDS/DEBUG
    ###########################################################################
    @commands.command()
    async def parsecrash(self, ctx, url: str):
        """
        Debug command to manually parse a crash report,
        returning the summary in chat (no embed).
        """
        summary = await self.parse_crash_report_summary(url)
        await ctx.send(summary[:2000])  # must be under Discord's 2k message limit

async def setup(bot: commands.Bot):
    """
    Standard setup function for a Red cog.
    """
    cog = MediaAnalyzerAssistant(bot)
    bot.add_cog(cog)
