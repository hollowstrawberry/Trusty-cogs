import asyncio
import logging
from typing import Literal, Optional

import discord
import tweepy
from redbot.core import Config, checks, commands
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.chat_formatting import humanize_number
from redbot.core.utils.views import SetApiView

from .menus import BaseMenu, TweetListPages, TweetPages, TweetsMenu, TweetStreamView
from .tweets_api import USER_FIELDS, TweetsAPI

_ = Translator("Tweets", __file__)

log = logging.getLogger("red.trusty-cogs.Tweets")


@cog_i18n(_)
class Tweets(TweetsAPI, commands.Cog):
    """
    Cog for displaying info from Twitter's API
    """

    __author__ = ["Palm__", "TrustyJAID"]
    __version__ = "3.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 133926854, force_registration=True)
        default_global = {
            "error_channel": None,
            "error_guild": None,
            "schema_version": 0,
        }
        self.config.register_global(**default_global)
        self.config.register_channel(
            followed_accounts={},
            followed_str={},
            followed_rules={},
            guild_id=None,
            add_buttons=True,
        )
        self.config.register_user(tokens={})
        self.mystream = None
        self.run_stream = True
        self.twitter_loop = None
        self.stream_task = None
        self.accounts = {}
        self.dashboard_authed = {}
        self.tweet_stream_view: Optional[TweetStreamView] = None

    def format_help_for_context(self, ctx: commands.Context) -> str:
        """
        Thanks Sinbad!
        """
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nCog Version: {self.__version__}\ntweepy Version: {tweepy.__version__}"

    async def cog_unload(self) -> None:
        try:
            self.bot.remove_dev_env_value("tweets")
        except Exception:
            pass
        if self.tweet_stream_view:
            self.tweet_stream_view.stop()
        log.debug("Unloading tweets...")
        if self.twitter_loop:
            self.twitter_loop.cancel()
        log.debug("Twitter restart loop canceled.")
        self.run_stream = False
        if self.mystream is not None:
            log.debug("Twitter stream is running, trying to stop.")
            self.mystream.disconnect()
            log.debug("Twitter stream disconnected.")
        log.debug("Tweets unloaded.")

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        """
        Method for finding users data inside the cog and deleting it.
        """
        await self.config.user_from_id(user_id).clear()

    async def cog_load(self) -> None:
        if self.bot.owner_ids and 218773382617890828 in self.bot.owner_ids:
            try:
                self.bot.add_dev_env_value("tweets", lambda x: self)
            except Exception:
                pass
        self.twitter_loop = asyncio.create_task(self.start_stream())
        keys = await self.bot.get_shared_api_tokens("twitter")
        client_id = keys.get("client_id", None)
        client_secret = keys.get("client_secret", None)
        if client_id and client_secret:
            self.tweet_stream_view = TweetStreamView(cog=self)
            self.bot.add_view(self.tweet_stream_view)

    @commands.hybrid_group(name="twitter", aliases=["tweets", "tw"])
    async def _tweets(self, ctx: commands.Context):
        """Gets various information from Twitter's API"""
        pass

    @_tweets.group(name="stream")
    async def tweets_stream(self, ctx: commands.Context):
        """Controls for the twitter stream"""
        pass

    @_tweets.command(name="forgetme")
    async def delete_user_auth(self, ctx: commands.Context):
        """Delete your saved authentication data from the bot"""
        await self.red_delete_data_for_user(requester="user", user_id=ctx.author.id)
        await ctx.send(_("Your saved twitter authenication has been deleted."))

    @_tweets.command(name="send")
    async def send_tweet(self, ctx: commands.Context, *, message: str) -> None:
        """
        Allows the owner to send tweets through discord
        """
        if not await self.authorize_user(ctx):
            return
        api = await self.authenticate(ctx.author)
        try:
            await api.create_tweet(text=message[:280], user_auth=False)
        except Exception:
            log.error("Error sending tweet", exc_info=True)
            await ctx.send(_("An error has occured trying to send that tweet."))
            return
        await ctx.send(_("Tweet sent!"))

    @_tweets.command(name="trends")
    async def trends(self, ctx: commands.Context, *, location: str = "United States") -> None:
        """
        Gets twitter trends for a given location

        You can provide a location and it will try to get
        different trend information from that location
        default is `United States`
        """
        try:
            api = await self.authenticate()
        except MissingTokenError as e:
            await e.send_error(ctx)
            return
        try:
            fake_task = functools.partial(api.available_trends)
            task = self.bot.loop.run_in_executor(None, fake_task)
            location_list = await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            await ctx.send(_("Timed out getting twitter trends."))
            return
        country_id = None
        location_names = []
        for locations in location_list:
            location_names.append(locations["name"])
            if location.lower() in locations["name"].lower():
                country_id = locations
        if country_id is None:
            await ctx.send("{} Is not a correct location!".format(location))
            return
        try:
            fake_task = functools.partial(api.get_place_trends, country_id["woeid"])
            task = self.bot.loop.run_in_executor(None, fake_task)
            trends = await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            await ctx.send(_("Timed out getting twitter trends."))
            return
        em = discord.Embed(
            colour=await self.bot.get_embed_colour(ctx.channel),
            title=country_id["name"],
        )
        msg = ""
        trends = trends[0]["trends"]
        for trend in trends:
            # trend = trends[0]["trends"][i]
            if trend["tweet_volume"] is not None:
                msg += "{}. [{}]({}) Volume: {}\n".format(
                    trends.index(trend) + 1,
                    trend["name"],
                    trend["url"],
                    trend["tweet_volume"],
                )
            else:
                msg += "{}. [{}]({})\n".format(
                    trends.index(trend) + 1, trend["name"], trend["url"]
                )
        count = 0
        for page in pagify(msg[:5980], shorten_by=1024):
            if count == 0:
                em.description = page
            else:
                em.add_field(name=_("Trends (continued)"), value=page)
            count += 1
        em.timestamp = datetime.utcnow()
        if ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send(embed=em)
        else:
            await ctx.send("```\n{}```".format(msg[:1990]))

    async def get_twitter_user(self, username: str) -> tweepy.User:
        try:
            api = await self.authenticate()
            user = await api.get_user(
                username=username,
                user_fields=USER_FIELDS,
            )
        except asyncio.TimeoutError:
            raise
        except tweepy.errors.TweepyException:
            raise
        return user

    @_tweets.command(name="user", aliases=["getuser"])
    async def get_user_com(self, ctx: commands.Context, username: Optional[str] = None) -> None:
        """Get info about the specified user"""
        if not await self.authorize_user(ctx):
            return
        api = await self.authenticate(ctx.author)
        try:
            if username is None:
                resp = await api.get_me(user_fields=USER_FIELDS, user_auth=False)
            else:
                resp = await api.get_user(
                    username=username,
                    user_fields=USER_FIELDS,
                )
            user = resp.data
        except tweepy.errors.TweepyException:
            await ctx.send(_("{username} could not be found.").format(username=username))
            return
        profile_url = f"https://twitter.com/{user.username}"
        description = str(user.description)
        for url in user.entities["description"].get("urls", []):
            if str(url["url"]) in description:
                description = description.replace(url["url"], str(url["expanded_url"]))
        emb = discord.Embed(
            url=profile_url,
            description=str(description),
            timestamp=user.created_at,
        )
        emb.set_author(name=user.name, url=profile_url, icon_url=user.profile_image_url)
        emb.set_thumbnail(url=user.profile_image_url)
        emb.add_field(
            name="Followers", value=humanize_number(user.public_metrics["followers_count"])
        )
        emb.add_field(
            name="Following", value=humanize_number(user.public_metrics["following_count"])
        )
        if user.verified:
            emb.add_field(name="Verified", value="Yes")
        footer = "Created at "
        emb.set_footer(text=footer)
        if ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send("<" + profile_url + ">", embed=emb)
        else:
            await ctx.send(profile_url)

    @_tweets.command(name="tweets", aliases=["gettweets", "status"])
    async def get_tweets(self, ctx: commands.Context, username: Optional[str] = None) -> None:
        """
        Display a users tweets as a scrollable message
        """
        async with ctx.typing():
            if not await self.authorize_user(ctx):
                return
            api = await self.authenticate(ctx.author)
            if username is None:
                resp = await api.get_me(user_auth=False)
                username = resp.data.username
        await TweetsMenu(source=TweetPages(api=api, username=username), cog=self).start(ctx=ctx)

    @tweets_stream.command(name="follow")
    @commands.mod_or_permissions(manage_channels=True)
    async def add_follow_channel(
        self, ctx: commands.Context, channel: discord.TextChannel, username: str
    ):
        """
        Add a twitter username to follow in a channel.

        Note: This may not work if the username is not present in one of the stream rules.
        You can view existing rules with `[p]tweets stream rules`
        """
        resp = await self.get_twitter_user(username)
        if not resp.data:
            await ctx.send(
                _("I could not find a user named `{username}`.").format(username=username)
            )
            return
        user = resp.data
        async with self.config.channel(channel).followed_accounts() as accounts:
            if str(user.id) not in accounts:
                accounts[str(user.id)] = {}
        await self.config.channel(channel).guild_id.set(channel.guild.id)
        await ctx.send(
            _("Following tweets from {user} in {channel}.").format(
                user=user.username, channel=channel.mention
            )
        )

    @tweets_stream.command(name="followrule")
    @commands.mod_or_permissions(manage_channels=True)
    async def add_rule_channel(
        self, ctx: commands.Context, channel: discord.TextChannel, rule_tag: str
    ):
        """
        Add all tweets from a specific stream rule to a channel.
        """
        async with self.config.channel(channel).followed_rules() as accounts:
            if str(rule_tag) not in accounts:
                accounts[rule_tag] = {}
        await self.config.channel(channel).guild_id.set(channel.guild.id)
        await ctx.send(
            _("Following tweets from {rule} in {channel}.").format(
                rule=rule_tag, channel=channel.mention
            )
        )

    @tweets_stream.command(name="unfollowrule")
    @commands.mod_or_permissions(manage_channels=True)
    async def remove_rule_channel(
        self, ctx: commands.Context, channel: discord.TextChannel, rule_tag: str
    ):
        """
        Remove all tweets from a specific stream rule to a channel.
        """
        async with self.config.channel(channel).followed_rules() as accounts:
            if str(rule_tag) in accounts:
                del accounts[rule_tag]
        await self.config.channel(channel).guild_id.set(channel.guild.id)
        await ctx.send(
            _("Unfollowing tweets from {rule} in {channel}.").format(
                rule=rule_tag, channel=channel.mention
            )
        )

    @tweets_stream.command(name="buttons")
    @commands.mod_or_permissions(manage_channels=True)
    async def toggle_stream_buttons(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Toggle whether or not to apply like, retweet, and reply buttons to the channels
        twitter stream

        `<channel>` The channel you want to toggle buttons for.
        """
        current = await self.config.channel(channel).add_buttons()
        await self.config.channel(channel).add_buttons.set(not current)
        if current:
            await ctx.send(
                _("Like, retweet, and reply buttons disabled in {channel}.").format(
                    channel=channel.mention
                )
            )
        else:
            msg = _("I am already posting {username} in {channel}.").format(
                username=username, channel=channel.mention
            )
            await ctx.send(msg)

    @_autotweet.command(name="list")
    @commands.bot_has_permissions(embed_links=True)
    async def _list(self, ctx: commands.context) -> None:
        """Lists the autotweet accounts on the guild"""
        guild = ctx.message.guild
        async with ctx.typing():
            account_list = {}
            async for user_id, account in AsyncIter(self.accounts.items(), steps=50):
                for channel_id, channel_data in account.channels.items():
                    if chan := guild.get_channel(int(channel_id)):
                        chan_info = f"{account.twitter_name} - {channel_data}\n"
                        if chan not in account_list:
                            account_list[chan] = [chan_info]
                        else:
                            account_list[chan].append(chan_info)
            account_str = ""
            for chan, accounts in account_list.items():
                account_str += f"{chan.mention} - {humanize_list(accounts)}"
            embed_list = []
            for page in pagify(account_str):
                embed = discord.Embed(
                    title="Twitter accounts posting in {}".format(guild.name),
                    colour=await self.bot.get_embed_colour(ctx.channel),
                    description=page,
                )
                embed.set_author(name=guild.name, icon_url=guild.icon_url)
                embed_list.append(embed)
        if not embed_list:
            await ctx.send(_("There are no Twitter accounts posting in this server."))
            return
        await BaseMenu(source=TweetListPages(embed_list)).start(ctx=ctx)

    async def save_accounts(self) -> None:
        data = {str(k): v.to_json() for k, v in self.accounts.items()}
        await self.config.accounts.set(data)

    async def add_account(
        self, channel: discord.TextChannel, user_id: int, screen_name: str
    ) -> bool:
        """
        Adds a twitter account to the specified channel.
        Returns False if it is already in the channel.
        """

        if str(user_id) in self.accounts:
            if str(channel.id) in self.accounts[str(user_id)].channels:
                return False
            else:
                self.accounts[str(user_id)].channels[str(channel.id)] = ChannelData(
                    guild=channel.guild.id,
                    replies=False,
                    retweets=True,
                    embeds=True,
                )
                await self.save_accounts()
        else:
            channels = {str(channel.id): ChannelData(guild=channel.guild.id)}
            twitter_account = TweetEntry(
                twitter_id=user_id,
                twitter_name=screen_name,
                channels=channels,
                last_tweet=0,
            )
            self.accounts[str(user_id)] = twitter_account
            await self.save_accounts()
        return True

    def get_tweet_list(self, api: tweepy.API, owner: str, list_name: str) -> List[int]:
        cursor = -1
        list_members: list = []
        for member in tweepy.Cursor(
            api.get_list_members, owner_screen_name=owner, slug=list_name, cursor=cursor
        ).items():
            list_members.append(member)
        return list_members

    @_autotweet.command(name="addlist")
    async def add_list(
        self,
        ctx: commands.context,
        owner: str,
        list_name: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """
        Add an entire twitter list to a specified channel.

        The list must be public or the bot owner must own it.
        `owner` is the owner of the list's @handle
        `list_name` is the name of the list
        `channel` is the channel where the tweets will be posted
        """
        try:
            api = await self.authenticate()
        except MissingTokenError as e:
            await e.send_error(ctx)
            return
        try:
            fake_task = functools.partial(
                self.get_tweet_list, api=api, owner=owner, list_name=list_name
            )
            task = ctx.bot.loop.run_in_executor(None, fake_task)
            list_members = await asyncio.wait_for(task, timeout=30)
        except asyncio.TimeoutError:
            msg = _("Adding that tweet list took too long.")
            log.error(msg, exc_info=True)
            await ctx.send(msg)
            return
        except Exception:
            log.error("Error adding list", exc_info=True)
            msg = _("That `owner` and `list_name` " "don't appear to be available")
            await ctx.send(msg)
            return
        if channel is None:
            channel = ctx.channel
        own_perms = channel.permissions_for(ctx.me)
        if not own_perms.send_messages:
            await ctx.send(
                _("Like, retweet, and reply buttons enabled in {channel}.").format(
                    channel=channel.mention
                )
            )

    @tweets_stream.command(name="unfollow")
    @commands.mod_or_permissions(manage_channels=True)
    async def remove_follow_channel(
        self, ctx: commands.Context, channel: discord.TextChannel, username: str
    ):
        """
        Add a twitter username to follow in a channel.

        Note: This may not work if the username is not present in one of the stream rules.
        You can view existing rules with `[p]tweets stream rules`
        """
        resp = await self.get_twitter_user(username)
        if not resp.data:
            await ctx.send(
                _("I could not find a user named `{username}`.").format(username=username)
            )
            return
        user = resp.data
        await self.config.channel(channel).guild_id.set(channel.guild.id)
        async with self.config.channel(channel).followed_accounts() as accounts:
            if str(user.id) in accounts:
                del accounts[str(user.id)]
            else:
                await ctx.send(
                    _("Tweets from {user} are not being followed in {channel}").format(
                        user=user.username, channel=channel.mention
                    )
                )
                return
        await ctx.send(
            _("Unfollowing tweets from {user} in {channel}.").format(
                user=user.username, channel=channel.mention
            )
        )

    @tweets_stream.command(name="rules")
    async def stream_rules(self, ctx: commands.Context):
        """List the current stream rules"""
        try:
            response = await self.mystream.get_rules()
        except AttributeError:
            await ctx.send(
                _(
                    "The stream has not been setup yet, make sure the bot owner has setup their API tokens properly."
                )
            )
            return
        if not response.data:
            await ctx.send(_("No rules have been created yet."))
            return
        embeds = []
        for rule in response.data:
            title = f"{rule.tag} ({rule.id})" if rule.tag else f"{rule.id}"
            embeds.append(discord.Embed(title=title, description=rule.value))
        await BaseMenu(source=TweetListPages(embeds)).start(ctx)

    @tweets_stream.command(name="addrule")
    @commands.is_owner()
    async def add_stream_rule(self, ctx: commands.Context, tag: str, *, rule: str):
        """
        Create a stream rule

        `<tag>` The name of the rule for finding the rule later.
        `<rule>` The Filtered stream rule. Information about rules can be found here
        https://developer.twitter.com/en/docs/twitter-api/tweets/filtered-stream/integrate/build-a-rule
        """
        rule = tweepy.StreamRule(tag=tag, value=rule)
        resp = await self.mystream.add_rules(rule)
        if not resp.errors:
            await ctx.send(_("Rule created successfully."))
        else:
            error_msg = _("There was an issue with that rule.\n")
            for error in resp.errors:
                for detail in error.get("details", []):
                    error_msg += detail
            await ctx.send(error_msg)

    @tweets_stream.command(name="delrule", aliases=["deleterule", "remrule"])
    @commands.is_owner()
    async def delete_stream_rule(self, ctx: commands.Context, tag_or_id: str):
        """
        Delete a stream rule

        `<tag_or_id>` The rule tag or rule ID you want to delete.
        """
        rules = await self.mystream.get_rules()
        response = ""
        for rule in rules.data:
            if rule.tag == tag_or_id:
                resp = await self.mystream.delete_rules(rule.id)
                tag = f"{rule.tag} ({rule.id})" if rule.tag else f"{rule.id}"
                if not resp.errors:
                    response += _("Rule {tag} deleted.\n").format(tag=tag)
                else:
                    error_msg = _("There was an issue with that rule.\n")
                    for error in resp.errors:
                        for detail in error.get("details", []):
                            error_msg += detail
                    response += error_msg
            if rule.id == tag_or_id:
                resp = await self.mystream.delete_rules(rule.id)
                tag = f"{rule.tag} ({rule.id})" if rule.tag else f"{rule.id}"
                if not resp.errors:
                    response += _("Rule {tag} deleted.\n").format(tag=tag)
                else:
                    error_msg = _("There was an issue with that rule.\n")
                    for error in resp.errors:
                        for detail in error.get("details", []):
                            error_msg += detail
                    response += error_msg
        await ctx.send(response)

    @_autotweet.command(name="del", aliases=["delete", "rem", "remove"])
    async def _del(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        username: Optional[str],
    ) -> None:
        """
        Removes a twitter username to the specified channel

        `<channel>` The channel in which you want to remove twitter posts for.
        `[username]` Optional @handle name for the user you want to remove.
        If `username` is not provided all users posting in the provided channel
        will be removed.
        """
        try:
            api = await self.authenticate()
        except MissingTokenError as e:
            await e.send_error(ctx)
            return
        user_id: Optional[int] = None
        screen_name: Optional[str] = None
        if username:
            try:
                for status in tweepy.Cursor(api.user_timeline, id=username).items(1):
                    user_id = status.user.id
                    screen_name = status.user.screen_name
            except tweepy.errors.TweepyException as e:
                msg = (
                    _("Whoops! Something went wrong here. The error code is ") + f"{e} {username}"
                )
                log.error(msg, exc_info=True)
                await ctx.send(_("Something went wrong here! Try again"))
                return
        removed = await self.del_account(channel.id, user_id, screen_name)
        if removed:
            accounts = humanize_list([i for i in removed.values()])
            msg = _("The following users have been removed from {channel}:\n{accounts}").format(
                channel=channel.mention, accounts=accounts
            )
            await ctx.send(msg)
        else:
            await ctx.send(
                _("{username} doesn't seem to be posting in {channel}").format(
                    username=username, channel=channel.mention
                )
            )

    @commands.group(name="tweetset")
    @checks.admin_or_permissions(manage_guild=True)
    async def _tweetset(self, ctx: commands.Context) -> None:
        """Command for setting required access information for the API.

        1. Visit https://apps.twitter.com and apply for a developer account.
        2. Once your account is approved Create a standalone app and copy the
        **API Key and API Secret**.
        3. On the standalone apps page select regenerate **Access Token and Secret**
        and copy those somewhere safe.
        4. Do `[p]set api twitter
        consumer_key YOUR_CONSUMER_KEY
        consumer_secret YOUR_CONSUMER_SECRET
        access_token YOUR_ACCESS_TOKEN
        access_secret YOUR_ACCESS_SECRET`
        """
        pass

    @_tweetset.command(name="creds")
    @checks.is_owner()
    async def set_creds(
        self,
        ctx: commands.Context,
    ) -> None:
        """How to get and set your twitter API tokens."""
        msg = _(
            "1. Visit https://apps.twitter.com and apply for a developer account.\n"
            "2. Once your account is approved Create a Project\n"
            "3. Add an app to the project and copy the **Bearer Token** "
            "(Optionally) Copy the **Client ID** and **Client Secret**"
            "4. Under User authentication settings enable OAuth 2.0, customize the settings "
            "however you want the bot to work, and remember or set the redirect uri.\n\n"
            "5. Do `[p]set api twitter "
            "bearer_token YOUR_BEARER_TOKEN "
            "client_id YOUR_CLIENT_ID "
            "client_secret YOUR_CLIENT_SECRET "
            "redirect_uri YOUR_REDIRECT_URI_TOKEN`\n\n"
        )
        keys = {
            "bearer_token": "",
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "https://127.0.0.1/",
        }
        view = SetApiView("twitter", keys)
        if await ctx.embed_requested():
            em = discord.Embed(description=msg)
            await ctx.send(embed=em, view=view)
            # await ctx.send(embed=em)
        else:
            await ctx.send(msg, view=view)
