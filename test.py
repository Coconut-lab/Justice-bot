"""
복순이 제작소
"""

import disnake
from disnake.ext import commands
import asyncio
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
from enum import Enum
from typing import Literal, List, Dict, Tuple

load_dotenv()

client = AsyncIOMotorClient(os.getenv("TESTDBCLIENT"))
BOT_TOKEN = os.getenv("TESTBOTTOKEN")
db = client["Boksun_db"]
mute_logs_collection = db['mute_logs']
user_roles_collection = db['user_roles']
warnings_collection = db['warnings']
kick_logs_collection = db['kick_logs']
ban_logs_collection = db['ban_logs']

intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)

# MUTE_ROLE_ID = 795147706237714433
MUTE_ROLE_ID = 1272135394669891621  # 테스트
ADMIN_ROLE_ID = [789359681776648202, 1185934968636067921, 1101725365342306415]


class LogType(str, Enum):
    ALL = "all"
    WARNING = "경고"
    MUTE = "재갈"
    KICK = "추방"
    BAN = "사형"


class MuteManager:
    def __init__(self, db):
        self.db = db
        self.mute_collection = self.db['mute_tasks']
        self.active_mutes = {}

    async def mute_user(self, member: disnake.Member, guild: disnake.Guild, reason: str, end_time: datetime, muted_by: disnake.Member):
        try:
            mute_role = guild.get_role(MUTE_ROLE_ID)
            if not mute_role:
                print("재갈 역할을 찾을 수 없습니다.")
                return

            if mute_role in member.roles:
                print(f"{member}는 이미 재갈 상태입니다.")
                return

            current_roles = [role.id for role in member.roles if role.id != guild.id and role.id != MUTE_ROLE_ID]
            await user_roles_collection.update_one(
                {'user_id': member.id},
                {'$set': {'roles': current_roles}},
                upsert=True
            )

            roles_to_remove = [role for role in member.roles if role.id != guild.id and role.id != MUTE_ROLE_ID]
            await member.remove_roles(*roles_to_remove, reason="Mute")
            await member.add_roles(mute_role)

            # MongoDB에 뮤트 정보 저장
            await self.mute_collection.update_one(
                {'user_id': member.id},
                {'$set': {
                    'user_id': member.id,
                    'guild_id': guild.id,
                    'end_time': end_time,
                    'reason': reason,
                    'muted_by': muted_by.id
                }},
                upsert=True
            )

            # 비동기적으로 unmute 스케줄링
            self.active_mutes[member.id] = asyncio.create_task(self.schedule_unmute(member, guild, end_time))

        except disnake.Forbidden:
            print(f"봇에게 {member}를 뮤트할 권한이 없습니다.")
        except Exception as e:
            print(f"{member} 뮤트 중 오류 발생: {str(e)}")

    async def schedule_unmute(self, member: disnake.Member, guild: disnake.Guild, end_time: datetime):
        try:
            await asyncio.sleep((end_time - datetime.now()).total_seconds())
            await self.unmute_user(member, guild)
        except asyncio.CancelledError:
            pass

    async def unmute_user(self, member: disnake.Member, guild: disnake.Guild) -> bool:
        try:
            mute_role = guild.get_role(MUTE_ROLE_ID)
            if not mute_role:
                print("뮤트 역할을 찾을 수 없습니다.")
                return False

            if mute_role not in member.roles:
                print(f"{member}는 뮤트 상태가 아닙니다.")
                return False

            await member.remove_roles(mute_role)

            user_roles = await user_roles_collection.find_one({'user_id': member.id})
            if user_roles:
                roles_to_add = [guild.get_role(role_id) for role_id in user_roles['roles'] if
                                guild.get_role(role_id) is not None]
                await member.add_roles(*roles_to_add)
                await user_roles_collection.delete_one({'user_id': member.id})

            # MongoDB에서 뮤트 정보 제거
            await self.mute_collection.delete_one({'user_id': member.id})

            # 활성 뮤트에서 제거
            if member.id in self.active_mutes:
                self.active_mutes[member.id].cancel()
                del self.active_mutes[member.id]

            print(f"{member}의 뮤트가 해제되었습니다.")
            return True
        except disnake.Forbidden:
            print(f"봇에게 {member}의 뮤트를 해제할 권한이 없습니다.")
        except Exception as e:
            print(f"{member} 뮤트 해제 중 오류 발생: {str(e)}")
        return False

    async def load_mutes(self, bot):
        current_time = datetime.now()
        async for mute in self.mute_collection.find():
            guild = bot.get_guild(mute['guild_id'])
            if guild:
                member = guild.get_member(mute['user_id'])
                if member:
                    end_time = mute['end_time']
                    if end_time > current_time:
                        self.active_mutes[member.id] = asyncio.create_task(self.schedule_unmute(member, guild, end_time))
                    else:
                        await self.unmute_user(member, guild)


@bot.event
async def on_ready():
    print("Bot is Ready!")
    await mute_manager.load_mutes(bot)

# MuteManager 인스턴스 생성
mute_manager = MuteManager(db)


class LogPaginator(disnake.ui.View):
    def __init__(self, logs: List[Dict], category: str, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.logs = logs
        self.category = category
        self.page = 0
        self.max_page = (len(logs) - 1) // 5

    @disnake.ui.button(label="◀️", style=disnake.ButtonStyle.blurple)
    async def prev_page(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        self.page = max(0, self.page - 1)
        await self.update_message(inter)

    @disnake.ui.button(label="▶️", style=disnake.ButtonStyle.blurple)
    async def next_page(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        self.page = min(self.max_page, self.page + 1)
        await self.update_message(inter)

    @disnake.ui.button(label="메시지 삭제", style=disnake.ButtonStyle.red)
    async def delete_message(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer()
        await inter.delete_original_message()
        self.stop()

    async def update_message(self, inter: disnake.MessageInteraction):
        embed = self.create_embed()
        await inter.response.edit_message(embed=embed, view=self)

    def create_embed(self) -> disnake.Embed:
        embed = disnake.Embed(title=f"{self.category} 기록", color=disnake.Color.red())

        start = self.page * 5
        end = start + 5
        for log in self.logs[start:end]:
            timestamp = get_timestamp(log)
            reason = log.get('reason', '사유 없음')
            action = log.get('action', 'unknown')
            action_text = '추가' if action == 'add' else '삭제' if action == 'remove' else action
            embed.add_field(name=f"{timestamp} ({action_text})", value=reason, inline=False)

        embed.set_footer(text=f"페이지 {self.page + 1}/{self.max_page + 1}")
        return embed


def get_timestamp(entry: Dict) -> str:
    timestamp = entry.get('timestamp') or entry.get('warned_at') or entry.get('muted_at') or entry.get(
        'kicked_at') or entry.get('banned_at')
    if timestamp is None:
        return "날짜 정보 없음"
    return timestamp.strftime('%Y-%m-%d %H:%M:%S') if isinstance(timestamp, datetime) else str(timestamp)


def create_all_log_embed(member: disnake.Member, warnings: List[Dict], mutes: List[Dict], kicks: List[Dict],
                         bans: List[Dict]) -> disnake.Embed:
    embed = disnake.Embed(title=f"{member.name}의 처벌 기록", color=disnake.Color.red())

    categories = [
        ("경고", warnings, disnake.Color.yellow()),
        ("재갈", mutes, disnake.Color.orange()),
        ("퇴출", kicks, disnake.Color.red()),
        ("사형", bans, disnake.Color.dark_red())
    ]

    for category, logs, color in categories:
        log_text = ""
        for log in logs[:3]:  # 각 카테고리당 3개씩만 표시
            timestamp = get_timestamp(log)
            reason = log.get('reason', '사유 없음')
            action = log.get('action', 'unknown')
            action_text = '추가' if action == 'add' else '삭제' if action == 'remove' else action
            log_text += f"• {timestamp} ({action_text}): {reason}\n"
        embed.add_field(name=f"{category} ({len(logs)}건)", value=log_text or "기록 없음", inline=False)

    return embed


async def add_kick_log(member: disnake.Member, reason: str, kicked_by: disnake.Member):
    await kick_logs_collection.insert_one({
        'user_id': member.id,
        'username': member.name,
        'guild_id': member.guild.id,
        'reason': reason,
        'kicked_at': datetime.now(),
        'kicked_by': {
            'id': kicked_by.id,
            'name': kicked_by.name
        }
    })


async def add_mute_log(member: disnake.Member, guild: disnake.Guild, reason: str, end_time: datetime,
                       muted_by: disnake.Member, count_mute: bool = True):
    mute_log = {
        'user_id': member.id,
        'username': member.name,
        'guild_id': guild.id,
        'reason': reason,
        'muted_at': datetime.now(),
        'end_time': end_time,
        'muted_by': {
            'id': muted_by.id,
            'name': muted_by.name
        },
        'action': 'mute' if count_mute else 'temp_mute'
    }
    await mute_logs_collection.insert_one(mute_log)
    return await get_punishment_counts(member.id)


"""
명령어 구간
경고, 경고삭제, 재갈
경고재갈, 재갈풀기
추방, 사형, 사면, 로그


아마도 필요할 예정인것들
금지어 추가, 금지어 삭제, 금지어 목록

수정할것들
뮤트해제 시간 DB에 저장된 시간을 기준으로 작동하게 해서 봇이 중간에 꺼졌다가 다시 켜져도 아무런 문제 없이 작동하도록 설계하기
"""


@bot.slash_command(name="경고", description="사용자에게 경고를 줍니다.")
async def warn(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.Member, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    warning_count, mute_count = await add_warning(멤버, inter.guild, 사유, inter.author)
    response = f"{멤버.mention}님에게 경고를 주었습니다. 사유: {사유}\n현재 경고 횟수: {warning_count}, 뮤트 횟수: {mute_count}"

    if warning_count % 3 == 0:
        mute_duration = timedelta(days=1)
        end_time = datetime.now() + mute_duration
        await mute_user_with_reason(멤버, inter.guild, "경고 3회 누적", end_time, inter.author)
        warning_count, mute_count = await add_mute_log(멤버, inter.guild, "경고 3회 누적", end_time, inter.author)
        response += f"\n경고 3회 누적으로 1일 재갈 처리되었습니다."

        if mute_count % 3 == 0:
            kick_reason = "뮤트 3회 누적"
            await 멤버.kick(reason=kick_reason)
            await add_kick_log(멤버, kick_reason, inter.author)
            response += f"\n재갈 3회 누적으로 퇴출 처리되었습니다."

    await inter.followup.send(response)


@bot.slash_command(name="경고삭제", description="사용자의 경고를 삭제합니다.")
async def remove_warning(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.Member, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    warning_count = await get_warning_count(멤버.id)
    if warning_count == 0:
        await inter.followup.send(f"{멤버.mention}님은 경고가 없습니다.")
        return

    await warnings_collection.insert_one({
        'user_id': 멤버.id,
        'username': 멤버.name,
        'guild_id': inter.guild.id,
        'reason': f"경고 삭제: {사유}",
        'warned_at': datetime.now(),
        'warned_by': {
            'id': inter.author.id,
            'name': inter.author.name
        },
        'action': 'remove'
    })

    new_warning_count = await get_warning_count(멤버.id)
    await inter.followup.send(f"{멤버.mention}님의 경고를 1회 삭제했습니다. 사유: {사유}\n현재 경고 수: {new_warning_count}")


@bot.slash_command(name="재갈", description="특정 사용자를 뮤트합니다.")
async def mute(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.Member, 뮤트시간: str, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    try:
        duration = parse_duration(뮤트시간)
        if duration is None:
            await inter.followup.send("재갈 시간 형식이 올바르지 않습니다. 예: 1h30m, 2d, 45m", ephemeral=True)
            return

        end_time = datetime.now() + duration
        await mute_user_with_reason(멤버, inter.guild, 사유, end_time, inter.author)
        warning_count, mute_count = await add_mute_log(멤버, inter.guild, 사유, end_time, inter.author)

        response = f"{멤버.mention}님을 {format_duration(duration)} 동안 입을 막아놨습니다. 사유: {사유}\n현재 경고 횟수: {warning_count}, 뮤트 횟수: {mute_count}"

        if mute_count % 3 == 0:
            kick_reason = "뮤트 3회 누적"
            await 멤버.kick(reason=kick_reason)
            await add_kick_log(멤버, kick_reason, inter.author)
            response += f"\n재갈 3회 누적으로 퇴출 처리되었습니다."

        await inter.followup.send(response)

    except Exception as e:
        await inter.followup.send(f"뮤트 중 오류가 발생했습니다: {str(e)}", ephemeral=True)


@bot.slash_command(name="경고재갈", description="특정 사용자에게 경고를 주고 뮤트합니다 (뮤트 카운트 증가 없음).")
async def warn_and_mute(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.Member, 뮤트시간: str, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    try:
        duration = parse_duration(뮤트시간)
        if duration is None:
            await inter.followup.send("재갈 시간 형식이 올바르지 않습니다. 예: 1h30m, 2d, 45m", ephemeral=True)
            return

        # 경고 추가
        warning_count, mute_count = await add_warning(멤버, inter.guild, f"경고재갈: {사유}", inter.author)

        # 경고 3회 누적 체크
        if warning_count % 3 == 0:
            duration = timedelta(days=1)  # 24시간 뮤트
            end_time = datetime.now() + duration
            await mute_user_with_reason(멤버, inter.guild, "경고 3회 누적", end_time, inter.author)
            await add_mute_log(멤버, inter.guild, "경고 3회 누적", end_time, inter.author, count_mute=True)
            response = f"{멤버.mention}님의 경고가 3회 누적되어 24시간 동안 재갈 처리되었습니다. 사유: 경고 3회 누적\n현재 경고 횟수: {warning_count}, 뮤트 횟수: {mute_count + 1}"
        else:
            # 일반적인 경고재갈 처리
            end_time = datetime.now() + duration
            await mute_user_with_reason(멤버, inter.guild, 사유, end_time, inter.author)
            await add_mute_log(멤버, inter.guild, 사유, end_time, inter.author, count_mute=False)
            response = f"{멤버.mention}님에게 경고를 주고 {format_duration(duration)} 동안 재갈을 물렸습니다. 사유: {사유}\n현재 경고 횟수: {warning_count}, 뮤트 횟수: {mute_count}"

        await inter.followup.send(response)

    except Exception as e:
        await inter.followup.send(f"경고재갈 처리 중 오류가 발생했습니다: {str(e)}", ephemeral=True)


@bot.slash_command(name="재갈풀기", description="사용자의 뮤트를 해제합니다.")
async def unmute_command(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.Member, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    success = await unmute_user(멤버, inter.guild)
    if success:
        await mute_logs_collection.insert_one({
            'user_id': 멤버.id,
            'username': 멤버.name,
            'unmuted_at': datetime.now(),
            'reason': f"뮤트 해제: {사유}",
            'unmuted_by': {
                'id': inter.author.id,
                'name': inter.author.name
            },
            'action': 'unmute'
        })
        await inter.followup.send(f"{멤버.mention}님의 재갈을 풀었습니다. 사유: {사유}")
    else:
        await inter.followup.send(f"{멤버.mention}님은 재갈 상태가 아닙니다.")


@bot.slash_command(name="추방", description="사용자를 서버에서 추방합니다.")
async def kick(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.Member, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    await 멤버.kick(reason=사유)
    await add_kick_log(멤버, 사유, inter.author)
    kick_count = await get_kick_count(멤버.id)

    response = f"{멤버.mention}님을 서버에서 추방했습니다. 사유: {사유}\n현재 추방 횟수: {kick_count}"

    if kick_count % 2 == 0:
        print(f"{멤버.id} 추방 2회 누적으로 사형 처리")
        await 멤버.ban(reason="추방 2회 누적")
        await ban_logs_collection.insert_one({
            'user_id': 멤버.id,
            'username': 멤버.name,
            'guild_id': inter.guild.id,
            'reason': "킥 2회 누적",
            'banned_at': datetime.now(),
            'banned_by': {
                'id': inter.author.id,
                'name': inter.author.name
            }
        })
        response += "\n추방 2회 누적으로 사형 처리되었습니다."

    await inter.followup.send(response)


@bot.slash_command(name="사형", description="사용자를 서버에서 차단합니다.")
async def ban(inter: disnake.ApplicationCommandInteraction, 유저: disnake.User, 사유: str = "사유 없음"):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    try:
        await inter.guild.ban(유저, reason=사유)

        await ban_logs_collection.insert_one({
            'user_id': 유저.id,
            'username': 유저.name,
            'guild_id': inter.guild.id,
            'reason': 사유,
            'banned_at': datetime.now(),
            'banned_by': {
                'id': inter.author.id,
                'name': inter.author.name
            },
            'action': 'ban'
        })

        await inter.followup.send(f"{유저.name}(ID: {유저.id})님을 사형했습니다. 사유: {사유}\n-# 사유 수정을 원한다면 차지철에게 DM")
    except ValueError:
        await inter.followup.send("올바른 사용자 ID를 입력해주세요.", ephemeral=True)
    except Exception as e:
        await inter.followup.send(f"사형 중 오류가 발생했습니다: {str(e)}", ephemeral=True)


@bot.slash_command(name="사면", description="사용자의 사형을 해제합니다.")
async def unban(inter: disnake.ApplicationCommandInteraction, 아이디: str, 사유: str):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    try:
        user_id = int(아이디)
        banned_entry = await inter.guild.fetch_ban(disnake.Object(id=user_id))

        if banned_entry is None:
            await inter.followup.send(f"ID {user_id}인 사용자를 차단 목록에서 찾을 수 없습니다.", ephemeral=True)
            return

        await inter.guild.unban(banned_entry.user, reason=사유)

        await ban_logs_collection.insert_one({
            'user_id': user_id,
            'username': banned_entry.user.name,
            'guild_id': inter.guild.id,
            'reason': f"사면: {사유}",
            'unbanned_at': datetime.now(),
            'unbanned_by': {
                'id': inter.author.id,
                'name': inter.author.name
            },
            'action': 'unban'
        })

        await inter.followup.send(f"{banned_entry.user.name}(ID: {user_id})님을 사면했습니다. 사유: {사유}")
    except disnake.errors.NotFound:
        await inter.followup.send(f"ID {user_id}인 사용자를 차단 목록에서 찾을 수 없습니다.", ephemeral=True)
    except ValueError:
        await inter.followup.send("올바른 사용자 ID를 입력해주세요.", ephemeral=True)
    except Exception as e:
        await inter.followup.send(f"사형 해제 중 오류가 발생했습니다: {str(e)}", ephemeral=True)



@bot.slash_command(name="로그", description="사용자의 처벌 기록을 확인합니다.")
async def log(inter: disnake.ApplicationCommandInteraction, 멤버: disnake.User,
              종류: Literal["전체", "경고", "재갈", "추방", "사형"] = "전체"):
    await inter.response.defer()

    if not any(role.id in ADMIN_ROLE_ID for role in inter.author.roles):
        await inter.followup.send("이런건 내 주인님만 시킬 수 있다고.", ephemeral=True)
        return

    warnings = await get_log_entries(warnings_collection, 멤버.id)
    mutes = await get_log_entries(mute_logs_collection, 멤버.id)
    kicks = await get_log_entries(kick_logs_collection, 멤버.id)
    bans = await get_log_entries(ban_logs_collection, 멤버.id)

    if 종류 == "전체":
        embed = create_all_log_embed(멤버, warnings, mutes, kicks, bans)
        await inter.followup.send(embed=embed)
    else:
        logs = {
            "경고": warnings,
            "재갈": mutes,
            "추방": kicks,
            "사형": bans
        }[종류]

        if not logs:
            await inter.followup.send(f"{멤버.name}님의 {종류} 기록이 없습니다.")
        else:
            view = LogPaginator(logs, 종류)
            embed = view.create_embed()
            await inter.followup.send(embed=embed, view=view)


async def get_log_entries(collection, user_id: int) -> List[Dict]:
    return await collection.find({'user_id': user_id}).sort('timestamp', -1).to_list(length=None)


async def add_warning(member: disnake.Member, guild: disnake.Guild, reason: str, warned_by: disnake.Member):
    await warnings_collection.insert_one({
        'user_id': member.id,
        'username': member.name,
        'guild_id': guild.id,
        'reason': reason,
        'warned_at': datetime.now(),
        'warned_by': {
            'id': warned_by.id,
            'name': warned_by.name
        },
        'action': 'add'
    })
    return await get_punishment_counts(member.id)


async def get_warning_count(user_id: int) -> int:
    warnings = await warnings_collection.find({'user_id': user_id}).sort('warned_at', 1).to_list(length=None)
    count = 0
    for warning in warnings:
        if warning.get('action', 'add') == 'add':
            count += 1
        elif warning.get('action') == 'remove':
            count = max(0, count - 1)  # 경고 수가 음수가 되지 않도록 합니다
    return count


async def get_mute_count(user_id: int) -> int:
    mutes = await mute_logs_collection.find({'user_id': user_id}).to_list(length=None)
    return sum(1 for m in mutes if m.get('action') == 'mute')


async def get_kick_count(user_id: int) -> int:
    return await kick_logs_collection.count_documents({'user_id': user_id})


async def get_punishment_counts(user_id: int) -> Tuple[int, int]:
    warnings = await warnings_collection.count_documents({'user_id': user_id, 'action': 'add'})
    mutes = await mute_logs_collection.count_documents({'user_id': user_id, 'action': 'mute'})
    return warnings, mutes


async def mute_user_with_reason(member: disnake.Member, guild: disnake.Guild, reason: str, end_time: datetime,
                                muted_by: disnake.Member):
    await mute_manager.mute_user(member, guild, reason, end_time, muted_by)


async def schedule_unmute(member: disnake.Member, guild: disnake.Guild, end_time: datetime):
    await asyncio.sleep((end_time - datetime.now()).total_seconds())
    await unmute_user(member, guild)


async def unmute_user(member: disnake.Member, guild: disnake.Guild) -> bool:
    return await mute_manager.unmute_user(member, guild)


def parse_duration(duration_str: str) -> timedelta:
    total_seconds = 0
    current_number = ""
    for char in duration_str:
        if char.isdigit():
            current_number += char
        elif char in ['d', 'h', 'm']:
            if not current_number:
                return None
            value = int(current_number)
            if char == 'd':
                total_seconds += value * 86400
            elif char == 'h':
                total_seconds += value * 3600
            elif char == 'm':
                total_seconds += value * 60
            current_number = ""
        else:
            return None
    return timedelta(seconds=total_seconds) if total_seconds > 0 else None


def format_duration(duration: timedelta) -> str:
    days, remainder = divmod(duration.total_seconds(), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{int(days)}일")
    if hours > 0:
        parts.append(f"{int(hours)}시간")
    if minutes > 0:
        parts.append(f"{int(minutes)}분")

    return " ".join(parts) if parts else "1분 미만"


@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    if isinstance(error, commands.errors.CommandInvokeError):
        error = error.original

    error_message = f"명령어 실행 중 오류가 발생했습니다: {str(error)}"
    print(error_message)  # 콘솔에 오류 출력

    if not inter.response.is_done():
        await inter.response.send_message(error_message, ephemeral=True)
    else:
        await inter.followup.send(error_message, ephemeral=True)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
