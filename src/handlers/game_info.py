import asyncio
import time
from datetime import datetime, timedelta

import discord
import genshin as genshin
from discord.ext import commands
from sqlalchemy import select

from common.constants import Emoji, Time
from common.db import session
from common.genshin_server import ServerEnum
from common.logging import logger
from datamodels.diary_action import DiaryType, MoraAction, MoraActionId
from datamodels.genshin_user import GenshinUser
from interfaces import travelers_diary


class GameInfoHandler(commands.Cog):
    def __init__(self, bot: discord.Bot = None):
        self.bot = bot

    @commands.slash_command(
        description="Shows your current resin amount",
    )
    async def resin(
        self,
        ctx: discord.ApplicationContext,
    ):
        accounts = (
            session.execute(
                select(GenshinUser).where(GenshinUser.discord_id == ctx.author.id)
            )
            .scalars()
            .all()
        )

        if not accounts:
            await ctx.respond("You don't have any registered accounts with this bot.")
            return

        start = time.time()
        defer_task = asyncio.create_task(ctx.defer())

        embeds = []
        success = False
        for account in accounts:
            gs = account.client

            for uid in account.genshin_uids:
                embed = discord.Embed()
                embeds.append(embed)

                notes_task = asyncio.create_task(gs.get_notes(uid))
                diary_task = asyncio.create_task(self.get_diary_data(gs, uid))

                notes: genshin.models.Notes = await notes_task

                resin_capped = notes.current_resin == notes.max_resin

                embed.set_footer(
                    text=f"*Daily/weekly data is behind by 1 hour | UID-{str(uid)[-3:]}"
                )
                embed.add_field(
                    name=f"<:resin:907486661678624798> **{notes.current_resin}/{notes.max_resin}**",
                    value=(
                        ":warning: capped OMG"
                        if resin_capped
                        else f"capped <t:{int(notes.resin_recovery_time.timestamp())}:R>"
                    ),
                )

                if notes.expeditions:
                    exp_completed_at = max(exp.completion_time for exp in notes.expeditions)
                    exp_text = (
                        ":warning: all done"
                        if exp_completed_at <= datetime.now().astimezone()
                        else f"done <t:{int(exp_completed_at.timestamp())}:R>"
                    )
                else:
                    exp_text = ":warning: No ongoing expeditions"

                embed.add_field(
                    name=f"**{len(notes.expeditions)}/{notes.max_expeditions} expeditions dispatched**",
                    value=exp_text,
                )

                if notes.max_realm_currency:
                    embed.add_field(
                        name=f"**{notes.current_realm_currency}/{notes.max_realm_currency} realm currency**",
                        value=(
                            ":warning: capped OMG"
                            if notes.current_realm_currency == notes.max_realm_currency
                            else f"capped in <t:{int(notes.realm_currency_recovery_time.timestamp())}:R>"
                        ),
                        inline=False,
                    )

                embed.add_field(
                    name="\u200b",
                    value=f"{Emoji.LOADING} loading non-live data...",
                    inline=False,
                )

                await defer_task
                if not diary_task.done():
                    await ctx.edit(embeds=embeds)

                diary_data = await diary_task
                diary_data["Parametric transformer"] = self.parse_parametric_transformer(notes)

                embed.set_field_at(
                    len(embed.fields) - 1,
                    name="\u200b",
                    value="\n".join(
                        f"**{key}:** {val}" for key, val in diary_data.items()
                    ),
                    inline=False,
                )
                logger.info(f"Game info fetch time: {time.time() - start:.3f}s")
                await ctx.edit(embeds=embeds)
                success = True

        if not success:
            await ctx.edit(embed=discord.Embed(description="No UID found"))

    @staticmethod
    async def get_diary_data(client: genshin.GenshinClient, uid: int):
        server = ServerEnum.from_uid(uid)

        diary = travelers_diary.TravelersDiary(client, uid)
        weekly_logs = await diary.fetch_logs(DiaryType.MORA, server.last_weekly_reset)
        daily_logs = diary.get_logs(DiaryType.MORA, server.last_daily_reset)

        daily_commissions = 0
        daily_commission_bonus = 0
        random_events = 0
        weekly_bosses = 0
        weekly_bounties = 0
        elites = 0

        for action in daily_logs:
            if action.action == MoraAction.DAILY_COMMISSIONS:
                if action.action_id == 26:
                    daily_commission_bonus += 1
                else:
                    daily_commissions += 1
            elif action.action == MoraAction.RANDOM_EVENT:
                random_events += 1
            elif action.action == MoraAction.KILLING_MONSTER and action.amount in [
                200,
                600,
            ]:
                elites += 1

        for action in weekly_logs:
            if action.action == MoraAction.KILLING_BOSS and action.amount > 6000:
                # Killing weekly bosses at any AR will give more than 6k (min is 6375, max is 8100)
                weekly_bosses += 1
            if action.action_id == MoraActionId.REPUTATION_BOUNTY:
                weekly_bounties += 1

        bonus = (
            "bonus claimed" if daily_commission_bonus else ":warning: bonus unclaimed"
        )
        data = {
            "Daily commissions": (":warning: " if daily_commissions < 4 else "")
            + f"{daily_commissions}/4 ({bonus})",
            "Daily random events": (":warning: " if random_events < 10 else "")
            + f"{random_events}/10",
            "Daily elites": f"{elites}/400",
            "Weekly bosses": (":warning: " if weekly_bosses < 3 else "")
            + f"{weekly_bosses}/3",
            "Weekly bounties": (":warning: " if weekly_bounties < 3 else "")
            + f"{weekly_bounties}/3",
        }

        return data

    def parse_parametric_transformer(self, daily_notes: genshin.models.Notes):
        recovery_time = daily_notes.transformer_recovery_time

        if recovery_time < datetime.now().astimezone() \
                + Time.PARAMETRIC_TRANSFORMER_COOLDOWN - timedelta(minutes=1):
            return f"<t:{int(recovery_time.timestamp())}>"

        return ":warning: Transformer is ready"
