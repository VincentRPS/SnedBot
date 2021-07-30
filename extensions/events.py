import asyncio
import json
import logging
import uuid

import discord
from discord.ext import commands

from extensions.utils import components


async def has_owner(ctx):
    return await ctx.bot.custom_checks.has_owner(ctx)
async def has_priviliged(ctx):
    return await ctx.bot.custom_checks.has_priviliged(ctx)

class PersistentEventView(discord.ui.View):
    def __init__(self, bot:commands.Bot, buttons:list=None):
        super().__init__(timeout=None)
        self.bot = bot
        if buttons:
            for button in buttons:
                self.add_item(button)

class SignUpCategoryButton(discord.ui.Button):
    '''This button handles signing up a user & modifying the event-board embed'''
    def __init__(self, entry_id:int, category_name:str, emoji:discord.PartialEmoji, style:discord.ButtonStyle, label:str=None):
        super().__init__(style=style, label=label, emoji=emoji, custom_id=f"{entry_id}:{category_name}")
        self.entry_id = entry_id
        self.category_name = category_name
    
    async def refresh_embed_field(self, guild:discord.Guild, member_ids:list[int], embed:discord.Embed, field_name:str, member_cap:int=None) -> discord.Embed:
        insert_at = None
        inline = False
        member_cap = member_cap if member_cap else "∞"
        names = [guild.get_member(member_id).display_name for member_id in member_ids]
        names = names[: -(len(names)-5) or None] if names else ["-"] #Trim to last 5
        names = f"{names}`(...)`" if len(names) == 5 else names
        
        for i, field in enumerate(embed.fields):
            if field.name.startswith(field_name):
                inline = field.inline
                insert_at = i; break
        embed.insert_field_at(insert_at, name=f"{field_name} ({len(member_ids)}/{member_cap})", value="\n".join(names), inline=inline)
        embed.remove_field(insert_at+1)
        return embed

    #Called whenever the button is called
    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id:
            record = await self.view.bot.caching.get(table="events", guild_id=interaction.guild_id, msg_id=interaction.message.id, channel_id=interaction.channel.id)
            categories = json.loads(record["categories"][0])
            embed = interaction.message.embeds[0]
            remove_from = None
            state = "added"
            state_msgs = {
                "added": f"Added to category: **{self.category_name}**",
                "removed": f"Removed from category: **{self.category_name}**",
                "moved": f"Moved to category: **{self.category_name}**"
            }
            guild = self.view.bot.get_guild(interaction.guild_id)

            if interaction.user.id in categories[self.category_name]["members"]:
                #If removing
                categories[self.category_name]["members"].remove(interaction.user.id)
                embed = await self.refresh_embed_field(guild, categories[self.category_name]["members"], embed, self.category_name, categories[self.category_name]["member_cap"])
                state = "removed"
            else: #If adding
                for category, data in categories.items():
                    if category == self.category_name:
                        if data["member_cap"] and data["member_cap"] <= len(data["members"]):
                            return await interaction.response.send_message('This category is full!', ephemeral=True)
                        elif record["permitted_roles"][0] and not any(role_id in [role.id for role in interaction.user.roles] for role_id in record["permitted_roles"][0]):
                            return await interaction.response.send_message('You do not have permission to sign up to this event.', ephemeral=True)
                        else:  
                            categories[self.category_name]["members"].append(interaction.user.id)
                            embed = await self.refresh_embed_field(guild, categories[self.category_name]["members"], embed, self.category_name, categories[self.category_name]["member_cap"])

                for category, data in categories.items(): #Check if user is already in a team
                    if interaction.user.id in data["members"] and category != self.category_name:
                        state = "moved"
                        categories[category]["members"].remove(interaction.user.id)
                        embed = await self.refresh_embed_field(guild, categories[category]["members"], embed, category, categories[category]["member_cap"])
                        break

            async with self.view.bot.pool.acquire() as con:
                await con.execute('''
                UPDATE events SET categories = $1 WHERE guild_id = $2 AND entry_id = $3''',
                json.dumps(categories),
                guild.id,
                self.entry_id
                )
            await self.view.bot.caching.refresh(table="events", guild_id=guild.id)
            webhook = interaction.followup
            await interaction.response.edit_message(embed=embed)
            await webhook.send(state_msgs[state], ephemeral=True)


class Events(commands.Cog):
    '''
    Create and manage events that users can sign up to.
    '''
    def __init__(self, bot):
        self.bot = bot
        bot.loop.create_task(self.events_init())
        self.button_styles = {
            "Blurple": discord.ButtonStyle.primary,
            "Grey": discord.ButtonStyle.secondary,
            "Green": discord.ButtonStyle.success,
            "Red": discord.ButtonStyle.danger
        }


    async def events_init(self):
        '''Re-acquire all persistent buttons'''
        await self.bot.wait_until_ready()
        logging.info("Adding persistent views to events...")
        async with self.bot.pool.acquire() as con:
            records = await con.fetch('''SELECT * FROM events''')

        add_to_persistent_views = {}

        for record in records:
            for key, data in json.loads(record.get('categories')).items():
                button = SignUpCategoryButton(record.get('entry_id'), key, discord.PartialEmoji.from_str(data['emoji']), style=self.button_styles[data['buttonstyle']], label=data['buttonlabel'])
                if record.get('msg_id') not in add_to_persistent_views.keys():
                    add_to_persistent_views[record.get('msg_id')] = [button]
                else:
                    add_to_persistent_views[record.get('msg_id')].append(button)
                
        for msg_id, buttons in add_to_persistent_views.items():
            self.bot.add_view(PersistentEventView(self.bot, buttons), message_id=msg_id)

        logging.info('Events ready!')

    @commands.Cog.listener()
    async def on_event_timer_complete(self, timer):
        '''Event expiry'''
        entry_id = timer.notes
        record = await self.bot.caching.get(table="events", guild_id=timer.guild_id, entry_id=entry_id)
        guild = self.bot.get_guild(timer.guild_id); channel=guild.get_channel(timer.channel_id)
        if guild and channel:
            try:
                message = await channel.fetch_message(record["msg_id"][0])
            except discord.NotFound:
                return
            else:
                await message.edit(view=None)
                response_str = f"Event **'{message.embeds[0].title}'** is starting now!\n"
                for category, data in json.loads(record["categories"][0]).items():
                    members = [(guild.get_member(member_id)) for member_id in data['members']]
                    members = list(filter(None, members))
                    response_str=f"{response_str} \n**{category}: {', '.join([member.mention for member in members])}**"
                
                async with self.bot.pool.acquire() as con:
                    await con.execute('''DELETE FROM events WHERE guild_id = $1 AND entry_id = $2''', guild.id, entry_id)
                await self.bot.caching.refresh(table="events", guild_id=guild.id)

                if len(response_str) > 2000:
                    raise ValueError(f"So apparently there is a need for pagination, people are using events! response_str: {response_str}")
                else:
                    try:
                        await channel.send(response_str)
                    except discord.Forbidden:
                        pass

    @commands.group(help="Manages events.", description="Lists all events created in this guild, if any. Subcommands allow you to remove or set additional ones.", usage="buttonrole", invoke_without_command=True, case_insensitive=True)
    @commands.guild_only()
    @commands.check(has_priviliged)
    async def event(self, ctx):
        records = await self.bot.caching.get(table="events", guild_id=ctx.guild.id)
        if records:
            text = ""
            for i, rr_id in enumerate(records["entry_id"]):
                text = f"{text}**{rr_id}** - {ctx.guild.get_channel(records['channel_id'][i]).mention}\n"
            embed=discord.Embed(title="Events active in this server:", description=text, color=self.bot.embedBlue)
            await ctx.send(embed=embed)
        else:
            embed=discord.Embed(title="❌ Error: No active events", description=f"There are no active events in this server. Create one with `{ctx.prefix}event create`", color=self.bot.errorColor)
            await ctx.channel.send(embed=embed)


    @event.command(name="delete", aliases=["del", "remove"], help="Deletes an event by ID.", description="Deletes an event via it's ID. You can get the ID via the `event` command.", usage="event delete <ID>")
    @commands.guild_only()
    @commands.check(has_priviliged)
    async def event_delete(self, ctx, id:str):
            record = await self.bot.caching.get(table="events", guild_id=ctx.guild.id, entry_id = id)
            if record:
                channel = ctx.guild.get_channel(record['channel_id'][0])
                try:
                    message = await channel.fetch_message(record['msg_id'][0]) if channel else None
                    if message: #Remove button if the message still exists
                        await message.delete()
                except discord.NotFound:
                    pass

                async with self.bot.pool.acquire() as con:
                    await con.execute('''DELETE FROM events WHERE guild_id = $1 AND entry_id = $2''', ctx.guild.id, id)
                    await self.bot.caching.refresh(table="events", guild_id=ctx.guild.id)
                    embed=discord.Embed(title="✅ Event deleted", description="Event has been successfully deleted!", color=self.bot.embedGreen)
                    await ctx.channel.send(embed=embed)
            else:
                embed=discord.Embed(title="❌ Error: Not found", description="There is no event by that ID.", color=self.bot.errorColor)
                await ctx.channel.send(embed=embed)


    @event.command(name="create", aliases=["new", "setup", "add"], help="Initializes setup to create and schedule an event.", description="Initializes a setup to help you add a new event. Takes no arguments.", usage="event create")
    @commands.guild_only()
    @commands.check(has_priviliged)
    @commands.max_concurrency(1, per=commands.BucketType.guild,wait=False)
    async def event_setup(self, ctx):
        '''
        Here is where end-users would set up an event for their server
        '''
        records = await self.bot.caching.get(table="events", guild_id=ctx.guild.id)
        
        if records and len(records["entry_id"]) >= 10:
            embed=discord.Embed(title="❌ Error: Too many events", description="A server can only have up to **10** running events at a time.", color=self.bot.errorColor)
            await ctx.channel.send(embed=embed); return

        def idcheck(payload):
            return payload.author == ctx.author and payload.channel.id == ctx.channel.id
        def confirmemoji(reaction, user):
            return reaction.message.id == setup_msg.id and user.id == ctx.author.id


        options = []
        for channel in ctx.guild.channels:
            if channel.type in [discord.ChannelType.text, discord.ChannelType.news]:
                options.append(discord.SelectOption(label=f"#{channel.name}", value=channel.id))

        
        embed=discord.Embed(title="🛠️ Event setup", description="Please specify the channel where you want the event message to be sent!", color=self.bot.embedBlue)
        value, asked, setup_msg = await components.select_or_ask(ctx, options=options, placeholder="Select a channel", embed=embed)
        
        if value and not asked:
            event_channel = ctx.guild.get_channel(int(value["values"][0]))
        elif value and asked:
            try:
                event_channel = await commands.GuildChannelConverter().convert(ctx, value)
                if event_channel.type not in [discord.ChannelType.news, discord.ChannelType.text]:
                    embed=discord.Embed(title="❌ Error: Invalid channel", description="Channel must be of type `text` or `news`. Operation cancelled.", color=self.bot.errorColor)
                    await setup_msg.edit(embed=embed); return
            except commands.ChannelNotFound:
                embed=discord.Embed(title="❌ Error: Channel not found.", description="Unable to locate channel. Operation cancelled.", color=self.bot.errorColor)
                await setup_msg.edit(embed=embed);  return          
        
        else:
            raise asyncio.exceptions.TimeoutError

        embed=discord.Embed(title="🛠️ Event setup", description="What is the title of the event? Type it below! Please note that your title cannot exceed **100** characters.", color=self.bot.embedBlue)
        await setup_msg.edit(embed=embed, view=None)
        message = await self.bot.wait_for('message', timeout = 180.0, check=idcheck)
        if len(message.content) <= 100:
            event_title = message.content
        else:
            embed=discord.Embed(title="❌ Error: Title too long", description="Title cannot exceed **100** characters. Operation cancelled.", color=self.bot.errorColor)
            await setup_msg.edit(embed=embed);  return   
        await message.delete()

        embed=discord.Embed(title="🛠️ Event setup", description="Great! Now type the description of the event down below! Please note there is a maximum length of **2500** characters.", color=self.bot.embedBlue)
        await setup_msg.edit(embed=embed, view=None)
        message = await self.bot.wait_for('message', timeout = 300.0, check=idcheck)
        if len(message.content) <= 2500:
            event_description = message.content
        else:
            embed=discord.Embed(title="❌ Error: Description too long", description="Description cannot exceed **2500** characters. Operation cancelled.", color=self.bot.errorColor)
            await setup_msg.edit(embed=embed);  return   
        await message.delete()
        
        embed=discord.Embed(title="🛠️ Event setup", description="""When should the event end? Type it in below in one of the following formats:
        
        **Absolute:**
        `YYYY-MM-dd hh:mm`
        `YYYY-MM-dd`
        **Note:** Absolute times must be in UTC. [This website](https://www.timeanddate.com/worldclock/timezone/utc) may be of assistance.

        **Relative:**
        Examples:
        `in 5 days`
        `1 week`
        `2M`

        For more information about time and date formatting, please refer to the [documentation](https://sned.hypersden.com/docs/modules/reminders.html).
        """, color=self.bot.embedBlue)
        await setup_msg.edit(embed=embed, view=None)
        message = await self.bot.wait_for('message', timeout = 300.0, check=idcheck)
        try:
            event_expiry, string = await self.bot.get_cog("Timers").converttime(message.content)
        except ValueError as error:
            embed=discord.Embed(title="❌ Error: Date formatting error", description=f"Failed reading date. Operation cancelled.\n**Error:** ```{error}```", color=self.bot.errorColor)
            await setup_msg.edit(embed=embed);  return   
        await message.delete()


        '''Category adding'''
        categories = {}
        async def add_category(first:bool=False):
            if first:
                embed=discord.Embed(title="🛠️ Event Categories setup", description="Excellent! Now we will begin setting up the first category for this event! Please type the category's name below!\nMaximum **25** characters!\nExamples: `Red Team` or `Attackers`", color=self.bot.embedBlue)
            else:
                embed=discord.Embed(title="🛠️ Event Categories setup", description="Type the name of the category below!\nMaximum **25** characters!", color=self.bot.embedBlue)
            await setup_msg.edit(embed=embed, view=None)
            message = await self.bot.wait_for('message', timeout = 180.0, check=idcheck)
            if len(message.content) <= 25 or message.content not in categories.keys():
                category_name = message.content 
            elif message.content in categories.keys():
                embed=discord.Embed(title="❌ Error: Duplicate key", description="You already have a category with the same name. Operation cancelled.", color=self.bot.errorColor)
                await setup_msg.edit(embed=embed);  return 1
            else:
                embed=discord.Embed(title="❌ Error: Title too long", description="Category name cannot exceed **25** characters. Operation cancelled.", color=self.bot.errorColor)
                await setup_msg.edit(embed=embed);  return 1
            await message.delete()

            embed=discord.Embed(title="🛠️ Event Categories setup", description="React **to this message** with the emoji you want to appear on the sign-up button! This can be any emoji, be it custom or Discord default!", color=self.bot.embedBlue)
            await setup_msg.edit(embed=embed)
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0,check=confirmemoji)
            emoji = reaction.emoji
            await setup_msg.clear_reactions()

            view = discord.ui.View()
            options = []
            for name in self.button_styles.keys():
                options.append(discord.SelectOption(label=name))
            view.add_item(components.CustomSelect(placeholder="Select a style!", options=options))
            embed=discord.Embed(title="🛠️ Event Categories setup", description="Select the style of the sign-up button!", color=self.bot.embedBlue)
            await setup_msg.edit(embed=embed, view=view)
            await view.wait()
            if view.value:
                buttonstyle = view.value["values"][0]
            else:
                raise asyncio.exceptions.TimeoutError


            embed=discord.Embed(title="🛠️ Event Categories setup", description="Type in how many people should be able to join this category as a positive integer! If you do not wish to limit this, type `skip`.", color=self.bot.embedBlue)
            await setup_msg.edit(embed=embed, view=None)
            message = await self.bot.wait_for('message', timeout = 180.0, check=idcheck)
            await message.delete()
            try:
                member_cap = int(message.content) if message.content.lower() != "skip" else None
                if member_cap and (member_cap <= 0 or member_cap > 100):
                    raise ValueError
            except ValueError:
                embed=discord.Embed(title="❌ Error: Invalid value", description="Value must be positive integer below 100. Operation cancelled.", color=self.bot.errorColor)
                await setup_msg.edit(embed=embed);  return 1

            categories[category_name] = {
                "emoji": str(emoji),
                "buttonlabel": category_name,
                "buttonstyle": buttonstyle,
                "member_cap": member_cap,
                "members": []
            }
            if len(categories) < 9:
                embed=discord.Embed(title="🛠️ Event Categories setup", description="Category added! Would you like to add another?", color=self.bot.embedBlue)
                create_another = await ctx.confirm(embed=embed, delete_after=True)
                if create_another == True:
                    return await add_category(first=False)
                elif create_another == False:
                    return 0
                else:
                    raise asyncio.exception.TimeoutError
            else:
                return 0

        exit_code = await add_category(first=True)
        if exit_code != 0:
            return

        role_options = []
        for role in ctx.guild.roles:
            if role.name != "@everyone":
                rolename = role.name if len(role.name) <= 25 else role.name[:20] + "..."
                role_options.append(discord.SelectOption(label=rolename, value=role.id))

        event_permitted_roles = None
        class RolesView(discord.ui.View):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.skipping = False

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return ctx.author.id == interaction.user.id
                
            max_values = 5 if len(role_options) > 5 else len(role_options)
            @discord.ui.select(placeholder="Select some roles!", min_values=1, max_values=max_values, options=role_options)
            async def callback(self, select: discord.ui.Select, interaction: discord.Interaction):
                select.view.data = interaction.data

            @discord.ui.button(emoji='➡️', label='Skip', style=discord.ButtonStyle.blurple)
            async def skip(self, button: discord.ui.Button, interaction: discord.Interaction):
                button.view.skipping = True
                button.view.data = None
                button.view.stop()

            @discord.ui.button(emoji='✔️', label="Confirm", style=discord.ButtonStyle.green)
            async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
                if button.view.data is not None:
                    button.view.stop()
                else:
                    interaction.response.send_message('If you want to skip this step, press `Skip`.', ephemeral=True)

        if len(role_options) <= 25:
            embed=discord.Embed(title="🛠️ Event setup", description="Select up to 5 roles that are allowed to sign up to this event! Press `Skip` if you want anyone to be able to sign up!", color=self.bot.embedBlue)
            view = RolesView()
            await setup_msg.edit(embed=embed, view=view)
            await view.wait()
            if not view.skipping and view.data:
                event_permitted_roles = [int(value) for value in view.data["values"]]
            elif view.skipping:
                event_permitted_roles = None
            else:
                raise asyncio.exceptions.TimeoutError
        
        #entry_id is assigned manually because the button needs it before it is in the db
        entry_id = str(uuid.uuid4())


        event_embed = discord.Embed(title=event_title, description=event_description, color=discord.Color.gold())
        if event_permitted_roles:
            event_embed.add_field(name="Allowed roles", value=", ".join([ctx.guild.get_role(role_id).mention for role_id in event_permitted_roles]), inline=False)
        event_embed.add_field(name="Event start", value=f"{discord.utils.format_dt(event_expiry, style='F')} ({discord.utils.format_dt(event_expiry, style='R')})")
        buttons = []
        first = True
        for category, data in categories.items():
            button = SignUpCategoryButton(entry_id=entry_id, category_name=category, emoji=discord.PartialEmoji.from_str(data["emoji"]), style=self.button_styles[data["buttonstyle"]], label=data["buttonlabel"])
            buttons.append(button)
            member_cap = data["member_cap"] if data["member_cap"] else "∞"
            event_embed.add_field(name=f"{category} (0/{member_cap})", value="-", inline=True)
        event_embed.set_footer(text=f"Event created by {ctx.author}", icon_url=ctx.author.avatar.url)
        #Create message
        view = PersistentEventView(self.bot, buttons)
        event_msg = await event_channel.send(embed=event_embed, view=view)


        async with self.bot.pool.acquire() as con:
            await con.execute('''
            INSERT INTO events (entry_id, guild_id, channel_id, msg_id, recurring_in, permitted_roles, categories)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ''',entry_id, ctx.guild.id, event_channel.id, event_msg.id, None, event_permitted_roles, json.dumps(categories))
        await self.bot.caching.refresh(table="events", guild_id=ctx.guild.id)
        await self.bot.get_cog("Timers").create_timer(event_expiry, event="event", guild_id=ctx.guild.id, user_id=ctx.author.id, channel_id=event_channel.id, notes=entry_id)


        embed=discord.Embed(title="🛠️ Event setup", description="✅ Setup completed. Event created!", color=self.bot.embedGreen)
        await setup_msg.edit(embed=embed, view=None)
        


def setup(bot):
    logging.info("Adding cog: Events...")
    bot.add_cog(Events(bot))
