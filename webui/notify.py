# -*- coding: utf-8 -*-
"""把消息送出去：邮件（QQ 邮箱 / 163 / 学校邮箱）和企业微信、钉钉等群机器人。

为什么不做「QQ 机器人」：
  真正意义上的 QQ 机器人需要注册开发者账号、跑一个常驻服务并联网登录，
  在老师这台 Win7 机器上既装不了也维护不了。所以这里走两条真能用的路：
    1) 邮件 —— QQ 邮箱开一个「SMTP 授权码」就能发，能带附件，稳定可靠；
    2) 群机器人 webhook —— 企业微信/钉钉群里点两下就能拿到一个网址，粘贴进来即可。
  用 QQ 客户端本身发消息，则走桌面控制（desktop.send_qq_message），靠模拟操作完成，
  发完会截一张图让老师自己看结果，不属于「一定能送到」的通道。

只使用 Python 3.8.10 标准库（smtplib / email / urllib）。
"""

import email.header
import email.mime.application
import email.mime.multipart
import email.mime.text
import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.request

MAX_ATTACH_BYTES = 20 * 1024 * 1024
TIMEOUT = 45

QQ_HELP = ("QQ 邮箱：设置 → 账户 → 开启「POP3/SMTP 服务」后会给你一串 16 位授权码，"
           "把它填到「密码/授权码」里（QQ 邮箱不能用登录密码发信）。")


def config(settings):
    """读通知相关配置，返回 dict（都是字符串）。"""
    webui = getattr(settings, "webui", None) or {}
    notify = webui.get("notify") or {}
    out = {
        "smtp_host": str(notify.get("smtp_host") or "").strip(),
        "smtp_port": notify.get("smtp_port") or 465,
        "smtp_ssl": notify.get("smtp_ssl", True),
        "smtp_user": str(notify.get("smtp_user") or "").strip(),
        "smtp_password": str(notify.get("smtp_password") or ""),
        "mail_from": str(notify.get("mail_from") or "").strip(),
        "mail_to": str(notify.get("mail_to") or "").strip(),
        "webhook_url": str(notify.get("webhook_url") or "").strip(),
        "webhook_kind": str(notify.get("webhook_kind") or "wecom").strip().lower(),
    }
    if not out["mail_from"]:
        out["mail_from"] = out["smtp_user"]
    try:
        out["smtp_port"] = int(out["smtp_port"] or 465)
    except (TypeError, ValueError):
        out["smtp_port"] = 465
    return out


def mail_status(settings):
    """给设置界面用：邮件通道配好了没。"""
    conf = config(settings)
    missing = [name for name, value in (("SMTP 服务器", conf["smtp_host"]),
                                        ("邮箱账号", conf["smtp_user"]),
                                        ("授权码", conf["smtp_password"])) if not value]
    if missing:
        return False, "未配置（缺：%s）" % "、".join(missing)
    return True, "已配置：%s（%s:%s）" % (conf["smtp_user"], conf["smtp_host"], conf["smtp_port"])


def webhook_status(settings):
    conf = config(settings)
    if not conf["webhook_url"]:
        return False, "未配置"
    kind = {"wecom": "企业微信", "dingtalk": "钉钉", "feishu": "飞书", "custom": "自定义"}.get(
        conf["webhook_kind"], conf["webhook_kind"])
    return True, "已配置：%s 机器人" % kind


# ---------------------------------------------------------------------------
# 邮件
# ---------------------------------------------------------------------------
def _encode_header(value):
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return email.header.Header(value, "utf-8").encode()


def _one_line(value):
    """把邮件头里的换行压掉：主题/地址里夹 CR/LF 可以伪造出额外的邮件头（头注入）。"""
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()


ADDRESS_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def _clean_addresses(raw):
    """拆出收件人并做基本校验，返回 (合法列表, 被丢弃的原文列表)。

    中英文标点都认：老师很可能打「；」或「，」。
    """
    good, bad = [], []
    for item in re.split(r"[;,、，]", str(raw or "")):
        address = _one_line(item).strip().strip("<>").strip()
        if not address:
            continue
        if ADDRESS_RE.match(address):
            good.append(address)
        else:
            bad.append(address[:60])
    return good, bad


def send_email(settings, subject, body, to="", attach=None):
    """发一封邮件。to 留空就用配置里的默认收件人。返回 (信息, 错误)。"""
    conf = config(settings)
    ok, message = mail_status(settings)
    if not conf["smtp_host"] or not conf["smtp_user"] or not conf["smtp_password"]:
        return None, ("邮件还没配置好：%s\n%s\n请在 WebUI 的「设置 → 通知」里填写。"
                      % (message, QQ_HELP))
    recipients, rejected = _clean_addresses(to or conf["mail_to"])
    if rejected:
        return None, ("收件人地址不合法：%s（地址里不能有换行、空格或多余的符号）"
                      % "、".join(rejected))
    if not recipients:
        return None, "没有收件人：请在设置里填默认收件人，或在调用时给出 to。"
    sender = _one_line(conf["mail_from"] or conf["smtp_user"])
    if not ADDRESS_RE.match(sender):
        return None, "发件地址不合法：%s（去「设置 → 通知 → 邮箱账号」检查）" % sender[:60]
    subject = _one_line(subject) or "来自 Study7Agent 的消息"
    body = str(body or "")
    files = [str(item) for item in (attach or []) if str(item).strip()]
    total = 0
    for path in files:
        if not os.path.isfile(path):
            return None, "附件不存在：%s" % path
        total += os.path.getsize(path)
    if total > MAX_ATTACH_BYTES:
        return None, "附件一共 %.1f MB，超过 %d MB 上限。" % (total / 1048576.0,
                                                              MAX_ATTACH_BYTES // 1048576)
    message_object = email.mime.multipart.MIMEMultipart()
    message_object["From"] = _encode_header(sender)
    message_object["To"] = _encode_header(", ".join(recipients))
    message_object["Subject"] = _encode_header(subject)
    message_object.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
    for path in files:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            return None, "读不了附件 %s：%s" % (path, exc)
        part = email.mime.application.MIMEApplication(data)
        name = os.path.basename(path)
        part.add_header("Content-Disposition", "attachment",
                        filename=("utf-8", "", name))
        message_object.attach(part)
    payload = message_object.as_string()
    try:
        if conf["smtp_ssl"]:
            server = smtplib.SMTP_SSL(conf["smtp_host"], conf["smtp_port"],
                                      timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(conf["smtp_host"], conf["smtp_port"], timeout=TIMEOUT)
        try:
            server.ehlo()
            if not conf["smtp_ssl"]:
                try:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                except smtplib.SMTPException:
                    pass  # 学校内网服务器可能不支持 STARTTLS，直接明文继续
            server.login(conf["smtp_user"], conf["smtp_password"])
            server.sendmail(sender, recipients, payload)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
    except smtplib.SMTPAuthenticationError:
        return None, ("邮箱认证失败（用户名或授权码不对）。%s" % QQ_HELP)
    except smtplib.SMTPException as exc:
        return None, "发送邮件失败：%s（%s）" % (exc, _smtp_hint(exc))
    except (OSError, ssl.SSLError) as exc:
        return None, ("连不上邮件服务器 %s:%s —— %s。学校网络可能封了 465/587 端口，"
                      "可以换 587 + 关闭 SSL（STARTTLS）。"
                      % (conf["smtp_host"], conf["smtp_port"], exc))
    return ("已发送到 %s，主题「%s」%s" % ("、".join(recipients), subject,
                                          "，带 %d 个附件" % len(files) if files else "")), ""


def _smtp_hint(exc):
    code = getattr(exc, "smtp_code", None)
    return {550: "收件地址可能不存在或被拒收", 553: "发件地址不被服务器接受",
            554: "被判定为垃圾邮件，换个主题试试"}.get(code, "可稍后重试，或检查端口/SSL 设置")


# ---------------------------------------------------------------------------
# 群机器人 webhook
# ---------------------------------------------------------------------------
def send_webhook(settings, text, url="", kind=""):
    """给企业微信/钉钉/飞书群机器人发一条文本消息。返回 (信息, 错误)。"""
    conf = config(settings)
    target = str(url or conf["webhook_url"]).strip()
    kind = str(kind or conf["webhook_kind"] or "wecom").strip().lower()
    text = str(text or "").strip()
    if not target:
        return None, ("还没配置群机器人地址。企业微信：群 → 群机器人 → 添加 → 复制 Webhook 地址；"
                      "钉钉：群设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 Webhook。"
                      "拿到后填到 WebUI 的「设置 → 通知 → 消息推送地址」。")
    if not text:
        return None, "要推送的内容是空的"
    if not target.lower().startswith(("http://", "https://")):
        return None, "推送地址必须以 http:// 或 https:// 开头"
    if kind == "custom":
        payload = {"text": text}
    elif kind == "feishu":
        payload = {"msg_type": "text", "content": {"text": text}}
    else:  # 企业微信和钉钉的文本消息格式是一样的
        payload = {"msgtype": "text", "text": {"content": text}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(target, data=data, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(200000).decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", "replace")
        except Exception:
            pass
        return None, "推送失败：HTTP %s %s %s" % (exc.code, exc.reason, detail)
    except (urllib.error.URLError, OSError) as exc:
        return None, "推送失败：连不上 %s（%s）" % (target[:60], exc)
    # 企业微信/钉钉都是 HTTP 200 + 正文里带 errcode
    verdict = ""
    try:
        body = json.loads(raw) if raw.strip().startswith("{") else {}
        code = body.get("errcode", body.get("code", 0))
        if code not in (0, "0", None):
            verdict = "（服务器返回 errcode=%s %s）" % (code, body.get("errmsg") or body.get("msg") or "")
    except ValueError:
        pass
    names = {"wecom": "企业微信", "dingtalk": "钉钉", "feishu": "飞书", "custom": "自定义"}
    return "已推送到%s机器人（HTTP %s）%s" % (names.get(kind, kind), status, verdict), ""


def notify_settings_view(settings):
    """设置界面用的只读视图：不返回授权码原文，只说明配没配。"""
    conf = config(settings)
    mail_ok, mail_text = mail_status(settings)
    hook_ok, hook_text = webhook_status(settings)
    return {"smtpHost": conf["smtp_host"], "smtpPort": conf["smtp_port"],
            "smtpSsl": bool(conf["smtp_ssl"]), "smtpUser": conf["smtp_user"],
            "hasSmtpPassword": bool(conf["smtp_password"]),
            "mailFrom": conf["mail_from"], "mailTo": conf["mail_to"],
            "webhookUrl": conf["webhook_url"], "webhookKind": conf["webhook_kind"],
            "mailStatus": mail_text, "mailReady": mail_ok,
            "webhookStatus": hook_text, "webhookReady": hook_ok,
            "qqHelp": QQ_HELP}
