import discord
import asyncio
from discord.ext import pages

class AuthorOnlyView(discord.ui.View):
    '''A view that only responds to the author of the passed context'''

    def __init__(self, ctx, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx = ctx
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return self.ctx.author.id == interaction.user.id

class BackButtonView(AuthorOnlyView):
    '''A view typically used with errors, shows a single back button which changes View.value to "back" when pressed.'''
    def __init__(self, ctx, *args, **kwargs):
        super().__init__(ctx, *args, **kwargs)
        self.value = None
    
    @discord.ui.button(emoji="⬅️", label="Back", style=discord.ButtonStyle.blurple)
    async def callback(self, button:discord.ui.Button, interaction: discord.Interaction):
        self.value = "back"
        self.stop()

async def select_or_ask(ctx, options:list[discord.SelectOption], placeholder:str, embed:discord.Embed=None, content:str=None, message_to_edit:discord.Message=None):
    '''
    Helper function to work around limitations of select item length

    Attributes:
    options: List of options to insert into the select, if possible
    placeholder: Select placeholder
    embed: Embed to send via the message
    content: Message content, if any
    message_to_edit: A message to edit, if None, a new message will be sent

    Used mostly in the interactive setup commands across the bot.
    '''

    if not embed and not content:
        raise ValueError('Content or embed must not be None!')
        
    invalid_select = False
    for option in options:
        if len(option.label) > 25:
            invalid_select = True

    if len(options) <= 25 and not invalid_select:
        asked = False
        view = AuthorOnlyView(ctx)
        view.value = None
        view.add_item(CustomSelect(placeholder=placeholder, options=options))
        if not message_to_edit:
            msg = await ctx.send(content=content, embed=embed, view=view)
        else:
            await message_to_edit.edit(content=content, embed=embed, view=view)
            msg = None
        await view.wait()
        value = view.value if hasattr(view, "value") else None
        if msg:
            return (value, asked, msg)
        else:
            return (value, asked)
    else:
        asked = True
        if embed:
            embed.description = f"{embed.description}\nPlease type in your response below!"
        elif content:
            content = f"{content}\nPlease type in your response below!"

        def check(message):
            return message.author == ctx.author and message.channel == ctx.channel
        if not message_to_edit:
            msg = await ctx.channel.send(embed=embed, content=content, view=None)
        else:
            await message_to_edit.edit(embed=embed, content=content, view=None)
            msg = None
        try:
            message = await ctx.bot.wait_for('message', timeout=180.0, check=check)
            await message.delete()
            if msg:
                return (message.content, asked, msg)
            else:
                return (message.content, asked)
        except asyncio.exceptions.TimeoutError:
            return (None, asked)


class CustomSelect(discord.ui.Select):
    '''Select that returns it's data to the view'''
    
    def __init__(self, placeholder:str, options=[discord.SelectOption], min_values:int=1, max_values:int=1, response_msg:str=None):
        super().__init__(placeholder=placeholder, options=options, min_values=min_values, max_values=max_values)
        self.response_msg = response_msg
    
    async def callback(self, interaction: discord.Interaction):
        if self.response_msg:
            await interaction.response.send_message(self.response_msg, ephemeral=True)
        self.view.value = interaction.data
        self.view.stop()


class SnedMenuPaginator(pages.Paginator):
    '''Custom menu-styling for the bot'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.customize_button(button_name="first", button_emoji="⏮️", button_style=discord.ButtonStyle.blurple)
        self.customize_button(button_name="last", button_emoji="⏭️", button_style=discord.ButtonStyle.blurple)
        self.customize_button(button_name="next", button_emoji="▶️", button_style=discord.ButtonStyle.blurple)
        self.customize_button(button_name="prev", button_emoji="◀️", button_style=discord.ButtonStyle.blurple)
