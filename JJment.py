import disnake
from disnake.ext import commands
import motor.motor_asyncio
from datetime import datetime, timedelta
import asyncio
import os
from dotenv import load_dotenv

# MongoDB 연결 설정
client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["DBCLIENT"])
db = client["moderation_db"]
warnings_collection = db["warnings"]
restricted_users_collection = db["restricted_users"]

hanyang_admin = 789359681776648202
bot = commands.InteractionBot(intents=disnake.Intents.all())

async def check_and_punish(member: disnake.Member):
    user_warnings = await warnings_collection.find_one({"user_id": member.id})
    if not user_warnings:
        return

    warning_count = len(user_warnings["warnings"])

    if warning_count >= 5:
        await member.kick(reason="5회 이상 경고 누적")
        await warnings_collection.delete_one({"user_id": member.id})
    elif warning_count >= 3:
        await member.timeout(duration=3600, reason="3회 이상 경고 누적")

@bot.event
async def on_ready():
    print("Bot is ready")

# 경고 기능
@bot.slash_command(name="경고", description="부적절한 사용자에게 경고를 하나 추가합니다")
@commands.has_role(hanyang_admin)
async def warning(inter: disnake.ApplicationCommandInteraction, member: disnake.Member, reason: str = "사유 없음"):
    await inter.response.defer()

    # MongoDB에 경고 추가
    await warnings_collection.update_one(
        {"user_id": member.id},
        {"$push": {"warnings": {
            "reason": reason,
            "moderator": inter.author.id,
            "timestamp": datetime.utcnow().isoformat()
        }}},
        upsert=True
    )

    # 경고 수 가져오기
    user_warnings = await warnings_collection.find_one({"user_id": member.id})
    warning_count = len(user_warnings["warnings"]) if user_warnings else 1

    await check_and_punish(member)
    await inter.followup.send(f"{member.mention}님에게 경고를 부여했습니다. (현재 경고: {warning_count}회)\n사유: {reason}")

# 챗 뮤트 기능
@bot.slash_command(name="재갈", description="부적절한 사용자를 타임아웃 합니다. (분 단위로 입력받음)")
@commands.has_role(hanyang_admin)
async def mute(inter: disnake.ApplicationCommandInteraction, member: disnake.Member, duration: int, reason: str = "사유 없음"):
    await inter.response.defer()
    await member.timeout(duration=duration * 60, reason=reason)  # duration은 분 단위로 입력받음
    await inter.followup.send(f"{member.mention}님을 {duration}분 동안 채팅 금지했습니다.\n사유: {reason}")

# 챗 언뮤트 기능
@bot.slash_command(name="재갈풀기", description="부적절한 사용자를 타임아웃에서 벗어나도록 해줍니다.")
@commands.has_role(hanyang_admin)
async def unmute(inter: disnake.ApplicationCommandInteraction, member: disnake.Member):
    await inter.response.defer()
    await member.timeout(duration=None)
    await inter.followup.send(f"{member.mention}님 재갈을 풀었습니다!")

# 추방 기능
@bot.slash_command(name="추방", description="부적절한 사용자를 서버에서 추방합니다.")
@commands.has_role(hanyang_admin)
async def kick(inter: disnake.ApplicationCommandInteraction, member: disnake.Member, reason: str = "사유 없음"):
    await inter.response.defer()
    await member.kick(reason=reason)
    await inter.followup.send(f"{member.mention}님을 서버에서 추방했습니다.\n사유: {reason}")

# 영구 차단 기능
@bot.slash_command(name="영구차단", description="부적절한 사용자를 서버에서 차단합니다.")
@commands.has_role(hanyang_admin)
async def ban(inter: disnake.ApplicationCommandInteraction, member: disnake.Member, reason: str = "사유 없음"):
    await inter.response.defer()
    await member.ban(reason=reason)
    await inter.followup.send(f"{member.mention}님을 서버에서 영구 차단했습니다.\n사유: {reason}")

@bot.slash_command(name="경고확인", description="사용자 경고 누적을 확인합니다.")
@commands.has_role(hanyang_admin)
async def warning_check(inter: disnake.ApplicationCommandInteraction, member: disnake.Member):
    await inter.response.defer()
    user_warnings = await warnings_collection.find_one({"user_id": member.id})
    if not user_warnings or len(user_warnings["warnings"]) == 0:
        await inter.followup.send(f"{member.mention}님의 경고 기록이 없습니다.")
    else:
        warning_list = []
        for i, w in enumerate(user_warnings["warnings"]):
            timestamp = w.get("timestamp", "날짜 정보 없음")
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.rstrip('Z')).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass  # 날짜 변환에 실패하면 원래 문자열 사용
            elif isinstance(timestamp, datetime):
                timestamp = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            warning_list.append(f"{i + 1}. {w['reason']} - {timestamp}")

        warnings_text = "\n".join(warning_list)
        await inter.followup.send(f"{member.mention}님의 경고 기록 (총 {len(user_warnings['warnings'])}회):\n{warnings_text}")

@bot.slash_command(name="경고삭제", description="경고 하나를 삭제합니다.")
@commands.has_role(hanyang_admin)
async def 경고삭제(inter: disnake.ApplicationCommandInteraction, member: disnake.Member, warning_index: int = None):
    await inter.response.defer()

    user_warnings = await warnings_collection.find_one({"user_id": member.id})
    if not user_warnings or len(user_warnings["warnings"]) == 0:
        await inter.followup.send(f"{member.mention}님의 경고 기록이 없습니다.")
        return

    if warning_index is None:
        # 모든 경고 삭제
        await warnings_collection.delete_one({"user_id": member.id})
        await inter.followup.send(f"{member.mention}님의 모든 경고를 삭제했습니다.")
    else:
        # 특정 경고 삭제
        if 1 <= warning_index <= len(user_warnings["warnings"]):
            warning = user_warnings["warnings"][warning_index - 1]
            await warnings_collection.update_one(
                {"user_id": member.id},
                {"$pull": {"warnings": warning}}
            )

            timestamp = warning.get("timestamp", "날짜 정보 없음")
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.rstrip('Z')).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass
            elif isinstance(timestamp, datetime):
                timestamp = timestamp.strftime('%Y-%m-%d %H:%M:%S')

            await inter.followup.send(f"{member.mention}님의 {warning_index}번 경고를 삭제했습니다.\n"
                                      f"삭제된 경고: {warning['reason']} - {timestamp}")
        else:
            await inter.followup.send(f"올바르지 않은 경고 번호입니다. 1부터 {len(user_warnings['warnings'])} 사이의 숫자를 입력해주세요.")

@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    if isinstance(error, commands.MissingRole):
        message = "저리가 (권한이 없습니다!)"
    else:
        message = f"오류가 발생했습니다: {str(error)}"

    if not inter.response.is_done():
        await inter.response.send_message(message, ephemeral=True)
    else:
        await inter.followup.send(message, ephemeral=True)

bot.run(os.getenv("BOTTOKEN"))