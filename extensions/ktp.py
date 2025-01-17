import logging

import discord
from discord.ext import commands

async def has_owner(ctx):
    return await ctx.bot.custom_checks.has_owner(ctx)

logger = logging.getLogger(__name__)

class KeepOnTop(commands.Cog, name="Keep On Top"):
    '''
    Keep-On-Top message functionality, currently only available
    to whitelisted guilds
    '''
    
    def __init__(self, bot):
        self.bot = bot

    
    async def cog_check(self, ctx):
        return ctx.guild.id in self.bot.whitelisted_guilds and await ctx.bot.custom_checks.has_permissions(ctx, 'admin_permitted')
    
    @commands.Cog.listener()
    async def on_message(self, message):
        '''
        Check if the message is no longer on top, delete the old, and move it to the "top" again
        Also update the message ID so we know which one to delete next time
        '''
        if self.bot.is_ready() and self.bot.caching.is_ready:
            if message.guild and message.guild.id in self.bot.whitelisted_guilds:
                records = await self.bot.caching.get(table="ktp", guild_id=message.guild.id)
                if records:
                    for record in records:
                        if record['ktp_channel_id'] == message.channel.id and record['ktp_content'] != message.content and record['ktp_msg_id'] != message.id:
                            channel = message.channel
                            previous_top = channel.get_partial_message(record['ktp_msg_id'])
                            try:
                                await previous_top.delete() #Necessary to put in a try/except otherwise on a spammy channel this might spam the console to hell
                            except discord.errors.NotFound:
                                return
                            new_top = await channel.send(content=record['ktp_content'])
                            await self.bot.pool.execute('''UPDATE ktp SET ktp_msg_id = $1 WHERE guild_id = $2 AND ktp_id = $3''', new_top.id, message.guild.id, record['ktp_id'])
                            await self.bot.caching.refresh(table="ktp", guild_id=message.guild.id)
                            break


    @commands.group(aliases=["ktp"], help="Lists all keep-on-top messages. Subcommands can add/remove them.", description="Helps you list/manage keep-on-top messages. Keep-on-top messages are messages that are always the last message in the given channel, effectively being pinned.", usage="keepontop", invoke_without_command=True, case_insensitive=True)
    async def keepontop(self, ctx):
        '''
        Lets you "pin" a message to the top of a channel by 
        it being removed & resent by the bot every time a new message is
        sent in that channel.
        '''
        records = await self.bot.caching.get(table="ktp", guild_id=ctx.guild.id)
        if records:
            text = ""
            for record in records:
                text = f"{text}**#{record['ktp_id']}** - {ctx.guild.get_channel(record['ktp_channel_id']).mention}\n"
            embed=discord.Embed(title="Keep-On-Top messages for this server:", description=text, color=self.bot.embedBlue)
            await ctx.send(embed=embed)
        else:
            embed=discord.Embed(title="❌ Error: No keep-on-top messages", description="There are no keep-on-top messages for this server.", color=self.bot.errorColor)
            await ctx.channel.send(embed=embed)

    @keepontop.command(name="add", aliases=["create", "new", "setup"], help="Initializes a setup to add a new keep-on-top message.", description="Initializes a setup to start configuring a new keep-on-top message. A server can have up to **3** keep-on-top messages. Takes no arguments.", usage="keepontop add")
    async def ktp_add(self, ctx):

        records = await self.bot.caching.get(table="ktp", guild_id=ctx.guild.id)
        
        if records and len(records) >= 5:
            embed=discord.Embed(title="❌ Error: Too many keep-on-top messages", description="A server can only have up to **5** keep-on-top message(s) at a time.\n__Note:__ If you deleted the keep-on-top message before deleting the entry, make sure to also delete the entry!", color=self.bot.errorColor)
            await ctx.channel.send(embed=embed)
            return

        embed=discord.Embed(title="🛠️ Keep-On-Top Setup", description="Specify the channel where you want to keep a message on the top by mentioning it!", color=self.bot.embedBlue)
        await ctx.channel.send(embed=embed)
        try :
            def check(payload):
                return payload.author == ctx.author and payload.channel.id == ctx.channel.id
            payload = await self.bot.wait_for('message', timeout=60.0, check=check)
            ktp_channel = await commands.TextChannelConverter().convert(ctx, payload.content)

            if records:
                for record in records:
                    if record["ktp_channel_id"] == ktp_channel.id:
                        embed=discord.Embed(title="❌ Error: Duplicate entry", description="You cannot have two keep-on-top messages in the same channel!", color=self.bot.errorColor)
                        await ctx.channel.send(embed=embed)
                        return

            embed=discord.Embed(title="🛠️ Keep-On-Top Setup", description=f"Channel set to {ktp_channel.mention}!", color=self.bot.embedBlue)
            await ctx.channel.send(embed=embed)

            embed=discord.Embed(title="🛠️ Keep-On-Top Setup", description="Now type in the message you want to be kept on top!", color=self.bot.embedBlue)
            await ctx.channel.send(embed=embed)
            payload = await self.bot.wait_for('message', timeout=300.0, check=check)
            ktp_content = payload.content
            first_top = await ktp_channel.send(ktp_content)

            await self.bot.pool.execute('''
            INSERT INTO ktp (guild_id, ktp_channel_id, ktp_msg_id, ktp_content)
            VALUES ($1, $2, $3, $4)
            ''', ctx.guild.id, ktp_channel.id, first_top.id, ktp_content)
            await self.bot.caching.refresh(table="ktp", guild_id=ctx.guild.id)

            embed=discord.Embed(title="🛠️ Keep-On-Top Setup", description=f"✅ Setup completed. This message will now be kept on top of {ktp_channel.mention}!", color=self.bot.embedGreen)
            await ctx.channel.send(embed=embed)

        except commands.ChannelNotFound:
            embed=discord.Embed(title="❌ Error: Unable to locate channel.", description="The setup process has been cancelled.", color=self.bot.errorColor)
            await ctx.channel.send(embed=embed)
            return
    
    @keepontop.command(name="delete", aliases=["del", "remove"], help="Removes a keep-on-top message.", description="Removes a keep-on-top message entry, stopping the bot from keeping it on top anymore. You can get the keep-on-top entry ID via the `keepontop` command.", usage="keepontop delete <ID>")
    async def ktp_delete(self, ctx, id:int):
        records = await self.bot.caching.get(table="ktp", guild_id=ctx.guild.id, ktp_id=id)
        if records:
            await self.bot.pool.execute('''DELETE FROM ktp WHERE guild_id = $1 AND ktp_id = $2''', ctx.guild.id, id)
            await self.bot.caching.refresh(table="ktp", guild_id=ctx.guild.id)
            embed=discord.Embed(title="✅ Keep-on-top message deleted", description="Keep-on-top message entry deleted and will no longer be kept in top!", color=self.bot.embedGreen)
            await ctx.channel.send(embed=embed)
        else:
            embed=discord.Embed(title="❌ Error: Not found", description="There is no keep-on-top entry by that ID.", color=self.bot.errorColor)
            await ctx.channel.send(embed=embed)






def setup(bot):
    logger.info("Adding cog: KeepOnTop...")
    bot.add_cog(KeepOnTop(bot))
