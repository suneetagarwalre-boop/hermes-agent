"""Jeff-only Discord ingress, prompt-independent authority and private delivery.

All business results stay in an attested own DM. Shared rooms receive only fixed
receipts/errors. Delivery intents survive duplicate events/restart; uncertain
sends are reconciled by nonce, never blindly repeated. Explicit status is recovery.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import UTC, datetime

import discord

from gateway import reside_scope as scope
from gateway import reside_v3 as app

logger=logging.getLogger(__name__)
TIMEOUT=120
SEND_TIMEOUT=8
_LOCKS={}


def principal(adapter,message):
    guild=getattr(message,'guild',None)
    dm=guild is None and isinstance(message.channel,discord.DMChannel)
    return app.Principal(home=getattr(adapter,'_reside_profile_home',None),
        user=str(message.author.id),bot=str(getattr(adapter._client.user,'id','')),
        guild=str(guild.id) if guild else '',channel=str(message.channel.id),
        is_bot=bool(getattr(message.author,'bot',True)),
        parent=str(getattr(message.channel,'parent_id',None) or ''),dm=dm,
        recipient=str(getattr(getattr(message.channel,'recipient',None),'id','')) if dm else '')


def addressed(adapter,message,p):
    if p.dm:return True
    # No quote/embed/forward/raw body can masquerade as a mention event. The
    # Discord mention list and inline token must agree; ignore quoted/code lines.
    plain=re.sub(r'```.*?```|`[^`]*`','',str(message.content),flags=re.DOTALL)
    text='\n'.join(line for line in plain.splitlines()
                   if not line.lstrip().startswith(('>','```')))
    raw=re.search(r'<@!?'+scope.BOT+r'>',text) is not None
    mentioned=any(str(getattr(m,'id',''))==scope.BOT for m in (message.mentions or []))
    if raw and mentioned:return True
    ref=getattr(message,'reference',None)
    resolved=getattr(ref,'resolved',None)
    if (resolved is not None and str(getattr(getattr(resolved,'author',None),'id',''))==scope.BOT
            and str(getattr(getattr(resolved,'channel',None),'id',''))==p.channel):
        return True
    # Fresh followups only in a conversation explicitly opened by Jeff, never
    # ambient mentions of other people or chat from any other actor.
    if message.mentions:return False
    key=(p.user,p.channel)
    opened=getattr(adapter,'_reside_v3_conversations',{}).get(key,0)
    return (opened>0 and asyncio.get_running_loop().time()-opened < 300) or bool(app.recent(p))


async def reply_target(adapter,message,p):
    ref=getattr(message,'reference',None)
    if (ref is None or not getattr(ref,'message_id',None)
            or str(getattr(ref,'channel_id',''))!=p.channel):return False
    try:
        source=await asyncio.wait_for(message.channel.fetch_message(ref.message_id),SEND_TIMEOUT)
        return str(source.author.id)==scope.BOT and str(source.channel.id)==p.channel
    except Exception:return False


def _content(adapter,body):
    from plugins.platforms.discord.recipient_contract import declare_discord_recipients,prepare_discord_message
    allowed=discord.AllowedMentions(everyone=False,roles=False,
        users=[discord.Object(id=int(scope.JEFF))],replied_user=False)
    extra=adapter.config.extra
    if extra.get('action_mentions') is True:
        body=prepare_discord_message(declare_discord_recipients(body,[scope.JEFF]),extra,allowed,require_contract=True)
    return body,allowed


async def safe_notice(adapter,message,p,reason):
    # No question, source title, count, doc, exception or business content here.
    notices={
        'received':'I received your Reside request. Results will be private; allow DMs or message me directly. Request ',
        'dm_blocked':'I could not deliver privately. Enable DMs from this server or message this bot directly, then resend your request. Request ',
        'not_allowed':'This room is not enabled for Reside requests. Use the command center, approved Reside project rooms, or my own DM. Request ',
        'config':'The Reside lane is not enabled here yet. This needs the gateway operator, not a new business-access approval. Request ',
        'unsupported':'Attachments/forwarded snapshots are not opened here. Paste the relevant text or ask about a verified Reside document. Request ',
        'error':'The Reside request did not complete. Retry it or use request status with its message ID. No completion is claimed. Request ',
        'timeout':'The Reside request is taking too long. Its receipt is saved; use request status with the original message ID to recover the result. Request ',
        'delivered':'Your private Reside response was delivered. Request ',
        'uncertain':'Private delivery could not be confirmed. Use request status with the original message ID to explicitly recover the result. Request ',
    }
    body,allowed=_content(adapter,notices[reason]+str(message.id))
    sent=await asyncio.wait_for(message.channel.send(content=body,allowed_mentions=allowed,suppress_embeds=True),SEND_TIMEOUT)
    logger.info('Reside V3 notice event=%s reason=%s agent_dispatch=false',message.id,reason)
    return str(sent.id)


async def _notice_once(adapter,message,p,reason):
    event=str(message.id)
    try:app.get(p,event,'notice-'+reason);return
    except FileNotFoundError:pass
    # Persist intent FIRST. If send acknowledgement is lost, a repeated inbound
    # must not ping the source channel again. Explicit retry is a new event.
    try:app.put(p,event,'notice-'+reason,{'reason':reason,'at':datetime.now(UTC).isoformat()})
    except FileExistsError:return
    try:await safe_notice(adapter,message,p,reason)
    except Exception:
        logger.warning('Reside V3 notice failed event=%s reason=%s agent_dispatch=false',event,reason)


async def private_delivery(adapter,message,p,dm,event,result):
    p.authorize()
    if str(getattr(getattr(dm,'recipient',None),'id',''))!=scope.JEFF:
        raise PermissionError('private destination mismatch')
    prepared,allowed=_content(adapter,result['body'])
    digest=hashlib.sha256(prepared.encode()).hexdigest()
    chunks=[prepared[i:i+1800] for i in range(0,len(prepared),1800)]
    for i,chunk in enumerate(chunks):
        suffix='delivery-'+str(result['attempt'])+'-'+str(i)
        try:
            delivered=app.get(p,event,suffix)
            if delivered['digest']!=digest or delivered['dm']!=str(dm.id):
                raise PermissionError('delivery binding mismatch')
            continue
        except FileNotFoundError:pass
        nonce=int(hashlib.sha256((event+':'+suffix).encode()).hexdigest()[:15],16)
        intent={'digest':digest,'dm':str(dm.id),'nonce':str(nonce)}
        uncertain=False
        try:app.put(p,event,suffix+'-intent',intent)
        except FileExistsError:
            if app.get(p,event,suffix+'-intent')!=intent:
                raise PermissionError('delivery intent mismatch')
            uncertain=True
        if uncertain:
            found=None
            async for prior in dm.history(limit=100):
                if str(getattr(prior,'nonce',''))==str(nonce) and str(prior.author.id)==scope.BOT:
                    found=prior;break
            if found is None:return False
            sent=found
        else:
            sent=await asyncio.wait_for(dm.send(content=chunk,allowed_mentions=allowed if i==0 else discord.AllowedMentions.none(),
                suppress_embeds=True,nonce=nonce),SEND_TIMEOUT)
        app.put(p,event,suffix,dict(intent,message_id=str(sent.id)))
    return True


async def _run(adapter,message,p):
    text=re.sub(r'<@!?'+scope.BOT+r'>','',str(message.content)).strip()
    if getattr(message,'attachments',None) or getattr(message,'message_snapshots',None):
        text='help'  # Never download/parse or persist attachments/forwarded data.
        unsupported=True
    else:unsupported=False
    try:app.claim(p,str(message.id),text)
    except (ValueError,PermissionError):
        await _notice_once(adapter,message,p,'unsupported');return
    if unsupported:
        await _notice_once(adapter,message,p,'unsupported');return
    await _notice_once(adapter,message,p,'received')
    try:
        dm=message.channel if p.dm else await asyncio.wait_for(message.author.create_dm(),SEND_TIMEOUT)
        if str(getattr(getattr(dm,'recipient',None),'id',''))!=scope.JEFF:
            raise PermissionError('private destination mismatch')
    except Exception:
        await _notice_once(adapter,message,p,'dm_blocked');return
    match=re.fullmatch(r'(?:reside\s+)?(request status|retry request)\s+([0-9]{15,22})',text,re.IGNORECASE)
    try:
        if match:
            event=str(message.id)
            result=app.outcome(p,event)
            if result is None:
                # Bind this explicit recovery request to one immutable response.
                # A later retry of the ORIGINAL must not turn a duplicate status
                # event into a new delivery or a different private work product.
                target=match[2]
                origin=app.recovery_principal(p,target)
                recovered=app.outcome(origin,target)
                if match[1].lower()=='retry request':
                    recovered=await asyncio.wait_for(asyncio.to_thread(app.execute,origin,target,retry=True),TIMEOUT)
                body=(recovered['body'] if recovered else
                    'Original request is still running or interrupted; no completion is claimed. Resend the original question for a fresh read-only attempt.')
                snapshot={'policy':app.POLICY_DIGEST,'event':event,'attempt':1,
                    'state':recovered['state'] if recovered else 'complete',
                    'body':body,'completed':datetime.now(UTC).isoformat()}
                try:app.put(p,event,'result-1',snapshot)
                except FileExistsError:pass
                result=app.outcome(p,event)
            # Explicit recovery has its OWN delivery receipt/nonces, never mutates
            # or replays ambiguous old sends. User asked for this copy.
        else:
            event=str(message.id)
            result=app.outcome(p,event)
            if result is None:result=await asyncio.wait_for(asyncio.to_thread(app.execute,p,event),TIMEOUT)
        if result is None:
            await _notice_once(adapter,message,p,'timeout');return
        if not await private_delivery(adapter,message,p,dm,event,result):
            await _notice_once(adapter,message,p,'uncertain');return
        if not p.dm:await _notice_once(adapter,message,p,'delivered')
    except TimeoutError:
        await _notice_once(adapter,message,p,'timeout')
    except discord.Forbidden:
        await _notice_once(adapter,message,p,'dm_blocked')
    except Exception:
        logger.warning('Reside V3 failed event=%s reason=operation_or_delivery agent_dispatch=false',message.id)
        await _notice_once(adapter,message,p,'error')


async def _handle(adapter,message):
    """Exact restricted ingress. Always consumed; NEVER normal-agent fallback."""
    p=principal(adapter,message)
    if (p.user!=scope.JEFF or p.bot!=scope.BOT or p.home!=scope.HOME or p.is_bot):return
    if message.type not in {discord.MessageType.default,discord.MessageType.reply}:return
    # Authorize the resource context before consulting any durable followup state.
    try:p.authorize()
    except PermissionError:
        if adapter._self_is_raw_mentioned(message):
            if not adapter._dedup.is_duplicate(str(message.id)):
                try:await safe_notice(adapter,message,p,'not_allowed')
                except Exception:logger.warning('Reside V3 room notice failed event=%s',message.id)
        return
    if not addressed(adapter,message,p) and not await reply_target(adapter,message,p):return
    try:
        p.authorize()
        allowed,ignored=adapter._get_allowed_channels(),adapter._get_ignored_channels()
        keys={p.channel,p.parent}
        if not p.dm and ((allowed and '*' not in allowed and not keys & allowed) or '*' in ignored or keys & ignored):
            raise PermissionError('channel config denied')
    except PermissionError:
        try:await safe_notice(adapter,message,p,'not_allowed')
        except Exception:logger.warning('Reside V3 rejected event=%s reason=room agent_dispatch=false',message.id)
        return
    if adapter.config.extra.get('reside_scoped_ops_v3') is not True:
        await safe_notice(adapter,message,p,'config');return
    logger.info('Reside V3 admitted event=%s profile=hermesdiscord pid=%s policy=%s policy_digest=%s agent_dispatch=false',
        message.id,os.getpid(),app.POLICY,app.POLICY_DIGEST)
    conversations=getattr(adapter,'_reside_v3_conversations',None)
    if conversations is None:conversations={};adapter._reside_v3_conversations=conversations
    conversations[p.user,p.channel]=asyncio.get_running_loop().time()
    key=(str(p.home),p.user,p.channel)
    lock=_LOCKS.setdefault(key,asyncio.Lock())
    if lock.locked():
        # Receipt is prompt-independent and bounded even while another model runs.
        try:app.claim(p,str(message.id),re.sub(r'<@!?'+scope.BOT+r'>','',str(message.content)).strip())
        except (ValueError,PermissionError):return
        await _notice_once(adapter,message,p,'received')
    async with lock:
        # Queued, previously receipted requests still need initial execution. The
        # persisted attempt marker (not whether request was inserted) owns that.
        await _run(adapter,message,p)


async def handle(adapter,message):
    """Error barrier consumes restricted ingress, never an owner-agent fallback."""
    try:await _handle(adapter,message)
    except Exception:
        logger.warning('Reside V3 ingress failed event=%s reason=policy_or_storage agent_dispatch=false',getattr(message,'id','unknown'))
        try:
            p=principal(adapter,message)
            if (p.home==scope.HOME and p.user==scope.JEFF and p.bot==scope.BOT and not p.is_bot
                    and not adapter._dedup.is_duplicate(str(message.id))):
                await safe_notice(adapter,message,p,'error')
        except Exception:
            logger.warning('Reside V3 failure receipt unavailable; operator intervention required')
