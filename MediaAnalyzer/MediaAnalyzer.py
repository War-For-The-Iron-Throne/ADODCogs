import json
import discord
from discord.ui import View, Button
from redbot.core import commands
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from io import BytesIO
import aiohttp
import re
import copy
import traceback
import datetime
import sys

###############################################################################
# Helper Functions
###############################################################################

def embed_size(embed: discord.Embed) -> int:
    """
    Convert an embed to a dict, then JSON-stringify it to measure
    its total size. We'll use this to ensure it doesn't exceed 6000.
    """
    as_dict = embed.to_dict()
    return len(json.dumps(as_dict))

def safe_line_split(text: str, max_len: int) -> list[str]:
    """
    Break 'text' into multiple lines, none of which exceed max_len.
    Naively chunk if a line is too long.
    """
    lines_out = []
    for raw_line in text.splitlines():
        raw_line = raw_line.rstrip('\r')
        while len(raw_line) > max_len:
            lines_out.append(raw_line[:max_len])
            raw_line = raw_line[max_len:]
        if raw_line:
            lines_out.append(raw_line)
    return lines_out

def build_embeds_for_section(
    section_name: str,
    content: str,
    max_line_len: int = 80,
    max_field_chars: int = 900,
    max_fields_per_embed: int = 5
) -> list[discord.Embed]:
    """
    Break 'content' into multiple Embeds, ensuring:
      1) Each line is <= max_line_len.
      2) Each field is <= max_field_chars.
      3) Each embed has <= max_fields_per_embed fields.
      4) The total JSON size of an embed is <= 6000.
      5) The first embed in a section gets the section name as title.
         Subsequent embeds for that same section have no title.
    """

    content = content.strip()
    if not content:
        return []

    # Replace backticks so we don't nest code blocks inside code blocks
    content = content.replace("```", "ʼʼʼ")

    # 1) Break the content into lines (each at most max_line_len chars)
    lines = safe_line_split(content, max_line_len)

    embeds = []
    current_embed = None
    used_title = False
    field_buffer = []
    fields_in_current_embed = 0

    def finalize_field(embed_obj: discord.Embed, lines_for_field: list[str]) -> bool:
        """
        Convert lines_for_field to a code-block string, attempt to add it
        as a field in embed_obj. If it doesn't fit (embed_size > 6000),
        return False to signal that we need a new embed first.
        """
        field_str = "\n".join(lines_for_field)
        field_value = f"```{field_str}```"

        test_embed = copy.deepcopy(embed_obj)
        test_embed.add_field(name="\u200b", value=field_value, inline=False)

        if embed_size(test_embed) <= 6000:
            embed_obj.add_field(name="\u200b", value=field_value, inline=False)
            return True
        return False

    def push_current_embed():
        """If the current embed has at least one field, store it."""
        if current_embed and len(current_embed.fields) > 0:
            if embed_size(current_embed) <= 6000:
                embeds.append(current_embed)

    # Start with a new embed
    current_embed = discord.Embed(color=discord.Color.blue())
    current_embed.title = section_name
    used_title = True
    fields_in_current_embed = 0

    for line in lines:
        # If adding this line to 'field_buffer' would exceed max_field_chars,
        # finalize the existing field now
        prospective_buffer = field_buffer + [line]
        if len("\n".join(prospective_buffer)) > max_field_chars:
            if field_buffer:
                # try adding as a field
                if not finalize_field(current_embed, field_buffer):
                    # if it didn't fit, push the old embed
                    push_current_embed()
                    # start a new embed
                    current_embed = discord.Embed(color=discord.Color.blue())
                    if not used_title:
                        current_embed.title = section_name
                        used_title = True
                    finalize_field(current_embed, field_buffer)
                field_buffer.clear()
                fields_in_current_embed = len(current_embed.fields)

            # if we've reached max_fields_per_embed, push & start a new one
            if fields_in_current_embed >= max_fields_per_embed:
                push_current_embed()
                current_embed = discord.Embed(color=discord.Color.blue())
                if not used_title:
                    current_embed.title = section_name
                    used_title = True
                fields_in_current_embed = 0

        # Now add the new line
        field_buffer.append(line)
        # If we already have max_fields_per_embed fields, finalize the buffer right away
        if len(current_embed.fields) >= max_fields_per_embed:
            if field_buffer:
                if not finalize_field(current_embed, field_buffer):
                    push_current_embed()
                    current_embed = discord.Embed(color=discord.Color.blue())
                    if not used_title:
                        current_embed.title = section_name
                        used_title = True
                    finalize_field(current_embed, field_buffer)
                field_buffer.clear()
                fields_in_current_embed = len(current_embed.fields)

            if len(current_embed.fields) >= max_fields_per_embed:
                push_current_embed()
                current_embed = discord.Embed(color=discord.Color.blue())
                if not used_title:
                    current_embed.title = section_name
                    used_title = True

    # After the loop, if there's anything left in 'field_buffer', finalize it
    if field_buffer:
        if not finalize_field(current_embed, field_buffer):
            push_current_embed()
            current_embed = discord.Embed(color=discord.Color.blue())
            if not used_title:
                current_embed.title = section_name
                used_title = True
            finalize_field(current_embed, field_buffer)
        field_buffer.clear()

    # push final embed if it has content
    if current_embed and len(current_embed.fields) > 0:
        if embed_size(current_embed) <= 6000:
            embeds.append(current_embed)

    return embeds

def chunk_embeds(embeds: list[discord.Embed], size=10) -> list[list[discord.Embed]]:
    """
    Discord only allows up to 10 embeds per message.
    We'll chunk the final list of embeds into 'pages' of up to 10.
    """
    pages = []
    for i in range(0, len(embeds), size):
        pages.append(embeds[i : i + size])
    return pages

###############################################################################
# Paginator
###############################################################################

class PaginatedEmbeds(View):
    """
    A paginator that cycles through 'pages,' where each page
    is a list of up to 10 discord.Embed objects.
    """
    def __init__(self, pages: list[list[discord.Embed]], invoker_id: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.pages = pages
        self.index = 0
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the original user to use this paginator.
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
            await interaction.followup.send(
                "You're already on the first page!", ephemeral=True
            )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
            await self.update_message(interaction)
        else:
            await interaction.followup.send(
                "You're already on the last page!", ephemeral=True
            )

###############################################################################
# The Cog
###############################################################################

class MediaAnalyzer(commands.Cog):
    """Analyze images, crash reports, and webpages for AI Assistant functionality."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    async def cog_load(self):
        print("MediaAnalyzer cog loaded successfully.")

    async def cog_unload(self):
        if self.session:
            await self.session.close()
        print("MediaAnalyzer cog unloaded and resources cleaned up.")

    async def fetch_webpage(self, url: str) -> dict:
        """
        Fetch a crash-report webpage and parse out:
          - Exception
          - Enhanced Stacktrace
          - Installed Modules
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return {"error": f"Failed to fetch webpage. HTTP Status: {response.status}"}

                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                full_text = soup.get_text()

                headings_pattern = (
                    r"(?:Exception|Enhanced Stacktrace|Installed Modules|"
                    r"Loaded BLSE Plugins|Involved Modules and Plugins|Assemblies|"
                    r"Native Assemblies|Harmony Patches|Log Files|Mini Dump|Save File|"
                    r"Screenshot|Screenshot Data|Json Model Data)"
                )

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

                exception_text = exception_match.group(1).strip() if exception_match else ""
                stacktrace_text = stacktrace_match.group(1).strip() if stacktrace_match else ""
                installed_modules_text = ""
                if modules_match:
                    modules_block = modules_match.group(1)
                    mod_lines = re.findall(r"^[+-]\s+(.*?)(?:\(|$)", modules_block, re.MULTILINE)
                    mod_names = [m.strip() for m in mod_lines if m.strip()]
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
        """Analyze an image (OCR) using pytesseract."""
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

    def build_pages(
        self,
        exception_text: str,
        stacktrace_text: str,
        installed_modules_text: str
    ) -> list[list[discord.Embed]]:
        """
        Build pages for the crash report by:
          1) Building multiple small embeds for each section,
          2) Combining them into a single list,
          3) Splitting them into pages of up to 10 embeds each.
        """
        exc_embeds = build_embeds_for_section(
            "Exception",
            exception_text,
            max_line_len=80,
            max_field_chars=900,
            max_fields_per_embed=5
        )
        stack_embeds = build_embeds_for_section(
            "Enhanced Stacktrace",
            stacktrace_text,
            max_line_len=80,
            max_field_chars=900,
            max_fields_per_embed=5
        )
        mods_embeds = build_embeds_for_section(
            "Installed Modules",
            installed_modules_text,
            max_line_len=80,
            max_field_chars=900,
            max_fields_per_embed=5
        )

        all_embeds = exc_embeds + stack_embeds + mods_embeds

        if not all_embeds:
            empty_embed = discord.Embed(
                title="No Crash Report Data Found",
                description="Could not find Exception, Enhanced Stacktrace, or Installed Modules.",
                color=discord.Color.red()
            )
            return [[empty_embed]]

        pages = chunk_embeds(all_embeds, size=10)
        return pages

    @commands.command(name="analyze")
    async def analyze_command(self, ctx, url: str):
        """
        Analyze either a crash report (if it includes "report.butr.link")
        or an image URL (OCR).
        """
        if "report.butr.link" in url.lower():
            data = await self.fetch_webpage(url)
            if "error" in data:
                return await ctx.send(data["error"])

            exception_text = data.get("exception", "")
            stacktrace_text = data.get("enhanced_stacktrace", "")
            installed_text = data.get("installed_modules", "")

            pages = self.build_pages(exception_text, stacktrace_text, installed_text)
            try:
                if len(pages) == 1:
                    await ctx.send(embeds=pages[0])
                else:
                    view = PaginatedEmbeds(pages, ctx.author.id)
                    await ctx.send(embeds=pages[0], view=view)

            except discord.HTTPException as e:
                # If it's the size error, gather all debug info
                if "Embed size exceeds" in str(e) or "50035" in str(e):
                    await self.handle_embed_error(ctx, pages, e)
                else:
                    raise  # Some other HTTPException
        else:
            # Possibly an image link
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
                        description=f"Content extracted from {url}",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Text Content", value=analysis["text"] or "No text found", inline=False)
                    embed.add_field(name="Resolution", value=f"{analysis['width']}x{analysis['height']}", inline=False)
                    await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(f"Error analyzing the media: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Auto-detect crash report links in messages and parse them."""
        if message.author.bot:
            return

        urls = [word for word in message.content.split() if word.startswith("http")]
        for url in urls:
            if "report.butr.link" in url.lower():
                data = await self.fetch_webpage(url)
                if "error" in data:
                    await message.reply(data["error"])
                    return

                exception_text = data.get("exception", "")
                stacktrace_text = data.get("enhanced_stacktrace", "")
                installed_text = data.get("installed_modules", "")

                pages = self.build_pages(exception_text, stacktrace_text, installed_text)
                try:
                    if len(pages) == 1:
                        await message.reply(embeds=pages[0])
                    else:
                        view = PaginatedEmbeds(pages, message.author.id)
                        await message.reply(embeds=pages[0], view=view)
                except discord.HTTPException as e:
                    if "Embed size exceeds" in str(e) or "50035" in str(e):
                        await self.handle_embed_error(message, pages, e)
                    else:
                        raise
                return

    async def handle_embed_error(self, msg_or_ctx, pages, error: discord.HTTPException):
        """
        When an embed is too large (HTTPException 50035: 'Embed size exceeds...'),
        collect ALL relevant info (user, channel, time, embed sizes, fields, JSON, etc.),
        and send a fallback .txt file with the debug info.
        """
        # If 'msg_or_ctx' is a Context, we can get author/channel differently than if it's a Message
        # Let's handle both cases:
        if isinstance(msg_or_ctx, commands.Context):
            channel = msg_or_ctx.channel
            author = msg_or_ctx.author
            guild_info = f"Guild: {msg_or_ctx.guild} (ID: {msg_or_ctx.guild.id if msg_or_ctx.guild else 'N/A'})"
            message_content = f"Command message content: {msg_or_ctx.message.content}"
        else:
            # It's a discord.Message
            channel = msg_or_ctx.channel
            author = msg_or_ctx.author
            guild_info = f"Guild: {msg_or_ctx.guild} (ID: {msg_or_ctx.guild.id if msg_or_ctx.guild else 'N/A'})"
            message_content = f"Message content: {msg_or_ctx.content}"

        # Build the debug log
        now_str = datetime.datetime.utcnow().isoformat()
        user_info = f"User: {author} (ID: {author.id})"
        channel_info = f"Channel: {channel} (ID: {channel.id})"
        time_info = f"Time (UTC): {now_str}"
        error_info = f"Exception: {error}\nTraceback:\n{traceback.format_exc()}"
        embed_debug_lines = []

        # Let's enumerate each page, embed, and field, measure sizes
        for page_idx, page_embeds in enumerate(pages):
            page_line = f"\n\n=== Page {page_idx} (Total Embeds: {len(page_embeds)}) ===\n"
            embed_debug_lines.append(page_line)
            for emb_idx, emb in enumerate(page_embeds):
                emb_size = embed_size(emb)
                emb_title = emb.title or "No Title"
                emb_line = f"  Embed #{emb_idx} | Title: '{emb_title}' | JSON size: {emb_size}"
                embed_debug_lines.append(emb_line)
                for fld_idx, fld in enumerate(emb.fields):
                    field_len = len(fld.value)
                    field_line = f"    Field #{fld_idx} length: {field_len}"
                    embed_debug_lines.append(field_line)
                # Also dump the entire embed JSON
                emb_as_json = json.dumps(emb.to_dict(), indent=2)
                embed_debug_lines.append(f"  Embed JSON:\n{emb_as_json}")

        embed_debug_str = "\n".join(embed_debug_lines)

        # Consolidate all info into one big string
        file_contents = (
            "====== EMBED ERROR (Exceeded 6000 chars) ======\n"
            f"{user_info}\n{channel_info}\n{guild_info}\n"
            f"{message_content}\n{time_info}\n\n"
            f"{error_info}\n\n"
            "====== Embeds Debug ======\n"
            f"{embed_debug_str}\n"
        )

        # Make an in-memory text file
        file_obj = BytesIO(file_contents.encode('utf-8'))
        file_obj.name = "embed_error_log.txt"

        # Send fallback
        try:
            await channel.send(
                content=(
                    "**ERROR**: An embed exceeded Discord's size limit (6000 JSON chars). "
                    "A full debug log is attached."
                ),
                file=discord.File(file_obj)
            )
        except Exception as e2:
            # If for some reason we can't even send a file, log to console
            print(f"[FATAL] Could not send fallback file. Original error: {error}, fallback error: {e2}")
            print(file_contents, file=sys.stderr)

        # Also log to console
        print("[EMBED SIZE ERROR] Could not send embed due to size limit.")
        print(file_contents, file=sys.stderr)

async def setup(bot):
    cog = MediaAnalyzer(bot)
    try:
        await bot.add_cog(cog)
    except Exception as e:
        if cog.session:
            await cog.session.close()
        raise e
