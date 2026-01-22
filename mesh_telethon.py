# pip install --upgrade telethon
# pip install "python-socks[asyncio]"

import asyncio
import os
import json
import sys
import requests
import traceback
from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
import logging
import threading
import time
from functools import partial
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.WARNING)

# Путь к конфигу рядом со скриптом
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "mesh_telethon_config.json")

# Значения по умолчанию (создаются при первом запуске, если конфига нет)
# Не редактируйте этот конфиг здесь! Запустите скрипт, и он создаст json-файл рядом со скриптом, редактируйте его!
DEFAULT_CONFIG = {
    "api_id": 0, # Подставляем собственные значения из `my.telegram.org/apps`
    "api_hash": "",
    "api_login": "",
    "use_proxy": False, 
    "proxy_type": "socks5", # обязательный протокол
    "proxy_addr": "0.0.0.0", # обязательный IP-адрес прокси
    "proxy_port": 0000, # обязательный номер порта прокси-сервера
    "proxy_username": "", # имя пользователя, если прокси требует авторизации (необязательно)
    "proxy_password": "", # пароль, если прокси-сервер требует авторизации (необязательно)
    "proxy_rdns": True, # использовать удаленное или локальное разрешение, по умолчанию удаленное (необязательно)
    "tg_chats_configs": [
        {
            "chat_id": "", # id чата - например, -100555555555
            "chat_alias_faked": "", # просто ярлык для удобства. Ни на что не влияет
            "prepoll_url": [ # эти коллбэки выполняются перед запуском скрипта
                # "", ""
            ], 
            "send_updates_to": "", # куда сгружать полученные от TG обновления, https://... Отправляется POST с телом {"message": {"text": "...", "message_id": "...", chat: {"id": "..."}, "from": {"username": "...", "first_name": "...", "last_name": "..."}, "date": ...unix}}
            "poll_replies_from": "", # откуда забирать ответные сообщения, отправляемые в TG https://... Ожидается ответ вида {"messages":[{"name":"Alice","date":"18.01 19:44","msg":"Foo","chat_id": "-10055555555"},{"name":"Bob","date":"18.01 19:44","msg":"Bar","chat_id": "-10055555555"}]}
            "poll_period_seconds": 30, # период, с которым опрашивается poll_replies_from
            "http_ignore_ssl_errors": False, 
        },
    ],
}

def load_or_create_config(path: str) -> dict:
    """
    Загружает конфиг или создаёт файл с примерами и завершает выполнение,
    чтобы пользователь мог заполнить корректные значения.
    """
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Создан пример конфигурации: {path}")
        print("       Отредактируйте его и запустите скрипт снова.")
        sys.exit(0)

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Подставим недостающие ключи дефолтами (без перезаписи)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg

# загрузка конфига
_config = load_or_create_config(CONFIG_PATH)

# Присваиваем значения (с запасными значениями из DEFAULT_CONFIG)
api_id = int(_config.get("api_id", DEFAULT_CONFIG["api_id"]))
api_hash = _config.get("api_hash", DEFAULT_CONFIG["api_hash"])
api_login = _config.get("api_login", DEFAULT_CONFIG["api_login"])
use_proxy = bool(_config.get("use_proxy", DEFAULT_CONFIG["use_proxy"]))
proxy_type = _config.get("proxy_type", DEFAULT_CONFIG["proxy_type"])
proxy_addr = _config.get("proxy_addr", DEFAULT_CONFIG["proxy_addr"])
proxy_port = int(_config.get("proxy_port", DEFAULT_CONFIG["proxy_port"]))
proxy_username = _config.get("proxy_username", DEFAULT_CONFIG["proxy_username"])
proxy_password = _config.get("proxy_password", DEFAULT_CONFIG["proxy_password"])
proxy_rdns = bool(_config.get("proxy_rdns", DEFAULT_CONFIG["proxy_rdns"]))
tg_chats_configs = _config.get("tg_chats_configs", DEFAULT_CONFIG["tg_chats_configs"]) or None

# class MeshTelethon:
#    def __init__(self, meshcore, worker_index, config) -> None:

# узнает тип чата по полученному чату
def get_chat_type(chat):
    if isinstance(chat, User):
        return "private"
    elif isinstance(chat, Chat):
        return "group"
    elif isinstance(chat, Channel):
        return "channel"
    else:
        return "unknown"

# коллбэк на получение сообщения
async def on_new_message(event):
    # print(event.stringify())

    # нужно сформировать json
    # {"message": {"text": "...", "message_id": "...", chat: {"id": "..."}, "from": {"username": "...", "first_name": "...", "last_name": "..."}, "date": ...unix}}

    msg = event.message
    sender = await event.get_sender()
    sender_data = {
        "username": getattr(sender, "username", None),
        "first_name": getattr(sender, "first_name", None),
        "last_name": getattr(sender, "last_name", None),
    } if sender else {}
    chat = await event.get_chat()

    # проверим тип чата
    chat_type = get_chat_type(chat)

    # проверяем, назначены ли для этого чата обработчики
    worker_params = False
    if tg_chats_configs:
        for worker_index, worker_cfg in enumerate(tg_chats_configs):
            try:
                worker_chat_id = str(worker_cfg.get("chat_id", ""))
                if worker_chat_id == str(chat.id):
                    worker_params = worker_cfg
                elif worker_chat_id == str(chat.username):
                    worker_params = worker_cfg
                    chat.id = str(chat.username)
                elif worker_chat_id == "@"+str(chat.username):
                    worker_params = worker_cfg
                    chat.id = "@"+str(chat.username)
                elif worker_chat_id == "-100"+str(chat.id):
                    chat.id = "-100"+str(chat.id)
                    worker_params = worker_cfg

            except Exception as e:
                print(f"Произошла ошибка: {e}")
                # tb = traceback.format_exc()
                # print(tb)
                break

    # собираем данные
    if worker_params:
        data = {
            "message": {
                "text": msg.text,
                "message_id": msg.id,
                "chat": {
                    "id": chat.id,
                    "type": chat_type,
                },
                "from": sender_data,
                "date": int(msg.date.timestamp())  # unix time
            }
        }

        # этот экземпляр - просто для распечатки, его нельзя передавать в отправку
        json_data = json.dumps(data, ensure_ascii=False)
        # print(json_data)

        # пробуем постить в телеграм
        send_result = False # дефолт

        send_updates_to = worker_params.get("send_updates_to", "")
        if send_updates_to != "":
            send_result = send_to_extmsngr(send_updates_to, data, worker_params)

        if send_result:
            print("on_new_message(): Отправлено вовне")
        else:
            print("on_new_message(): Не удалось отправить вовне")

    else:
        pass

# отправка json с апдейтом во внешнюю систему
def send_to_extmsngr(url, payload, worker_params):
    
    result = False

    verify_ssl = not bool(worker_params.get("http_ignore_ssl_errors", False))

    if not url:
        print("send_to_extmsngr(): url не задан - отправка во внешнюю систему пропущена")
        return result

    try:
        print("Отправка сообщения во внешнюю систему: \n", payload)
        resp = requests.post(
            url,
            json=payload,
            timeout=10,
            verify=verify_ssl
        )
        if resp.status_code == 200:
            print("Сообщение успешно отправлено во внешнюю систему.\n")
            result = True
        else:
            print(
                "External POST вернул статус %s: %s\n",
                resp.status_code,
                resp.text[:200],
            )
            result = False
    except Exception as exc:
        print(
            "Ошибка при отправке сообщения во внешнюю систему: %s\n", exc
        )
        result = False
        tb = traceback.format_exc()
        print(tb)
    finally:
        return result
    
# выполняет prepoll и запускает polling
def run_pre_poll_and_reply_polling(client, loop):
    if not tg_chats_configs:
        print("tg_chats_configs пуст или не задан")
        return

    for worker_cfg in tg_chats_configs:

        verify_ssl = not bool(worker_cfg.get("http_ignore_ssl_errors", False))

        # --- PREPOLL ---
        prepoll_urls = worker_cfg.get("prepoll_url")
        if isinstance(prepoll_urls, list):
            print("Запуск prepoll_url...")
            for url in prepoll_urls:
                if url:
                    try:
                        print(f"PREPOLL запрос: {url}")
                        resp = requests.get(url, timeout=10, verify=verify_ssl)
                        print(f"Ответ {resp.status_code}: {resp.text[:200]}")
                    except Exception as e:
                        print(f"Ошибка prepoll запроса {url}: {e}")

        # --- POLLING ---
        poll_url = worker_cfg.get("poll_replies_from", "")
        poll_period = int(worker_cfg.get("poll_period_seconds", 30))

        if poll_url:
            print(f"Запуск polling из {poll_url} каждые {poll_period} секунд")

            def poll_loop(url, period, worker_cfg):
                while True:
                    try:
                        resp = requests.get(url, timeout=10, verify=verify_ssl)
                        if resp.status_code == 200:
                            data = resp.json()  # ← здесь уже dict
                            print("Получены данные:", data)

                            # пример: преобразование сообщений в словари
                            messages = data.get("messages", [])
                            for msg in messages:
                                msg_dict = {
                                    #"name": msg.get("name"),
                                    "date": msg.get("date"),
                                    "msg": msg.get("msg"),
                                    "chat_id": msg.get("chat_id"),
                                }
                                print("Сообщение:", msg_dict)

                                # отправка в Telegram
                                asyncio.run_coroutine_threadsafe(
                                    send_to_telegram(client, msg_dict, worker_cfg),
                                    loop
                                )

                        else:
                            print(f"Polling вернул статус {resp.status_code}: {resp.text[:200]}")
                    except Exception as e:
                        print(f"Ошибка polling запроса {url}: {e}")

                    time.sleep(period)

            # запуск polling в отдельном потоке, чтобы не блокировать Telegram-клиент
            thread = threading.Thread(
                target=poll_loop,
                args=(poll_url, poll_period, worker_cfg),
                daemon=True,
            )
            thread.start()

async def send_to_telegram(client, msg_dict: dict, passed_worker_cfg: dict):

    # извлечь chat_id из worker_cfg.
    # извлечь chat_id из msg_dict.
    # сравнить с приведением к строке. Если совпадают - работаем
    # client.send_message(-100123456, 'Hello, group!')

    # проблема: polling делается на один и тот же url, и если мы забрали ВСЕ сообщения,
    # то в них могут оказаться сообщения для других chat_id,
    # а назначенным в следующих конфигах chat_id сообщения уже не прилетят.
    # нужно все полученные сообщения проверить на наличие для них конфига с таким же chat_id 

    result = False

    # извлечем текст сообщения
    msg_text = str(msg_dict.get("msg", ""))

    worker_params_from_msg = False

    # извлечем chat_id от сообщения
    msg_chat_id = ""
    if msg_dict:
        msg_chat_id = str(msg_dict.get("chat_id", ""))

    # здесь будет целевой chat_id
    worker_chat_id = ""

    if tg_chats_configs:
        for worker_index, worker_cfg in enumerate(tg_chats_configs):
            try:
                temporary_worker_chat_id = str(worker_cfg.get("chat_id", ""))
                if temporary_worker_chat_id == msg_chat_id:
                    worker_params_from_msg = worker_cfg
                    worker_chat_id = temporary_worker_chat_id
                    print("send_to_telegram(): нашли конфиг для этого chat_id")
                elif temporary_worker_chat_id == "-100"+msg_chat_id:
                    # удалим -100 из chat_id воркера
                    if isinstance(temporary_worker_chat_id, str) and temporary_worker_chat_id.startswith("-100"):
                        worker_chat_id = temporary_worker_chat_id[4:]
                        worker_params_from_msg = worker_cfg
                        print("send_to_telegram(): нашли конфиг для этого chat_id, но с удалением -100")

            except Exception as e:
                print(f"Произошла ошибка: {e}")
                # tb = traceback.format_exc()
                # print(tb)
                break
    else:
        worker_chat_id = ""
        if passed_worker_cfg:
            worker_chat_id = str(passed_worker_cfg.get("chat_id", ""))
            print("send_to_telegram(): откатили chat_id к passed_worker_cfg-версии")


    # вероятно, нашли worker_chat_id, проверяем соответствие и отправляем
    if msg_chat_id and worker_chat_id and msg_chat_id == worker_chat_id:
        try:
            send_result = await client.send_message(normalize_chat_id(msg_chat_id), msg_text)
            print(f"send_to_telegram(): сообщение отправлено, id={send_result.id}")
            if send_result.id:
                result = True
            else:
                result = False
        except Exception as e:
            print(f"send_to_telegram(): ошибка отправки: {e}")
            result = False
    else:
        print("send_to_telegram(): msg_chat_id != worker_chat_id, или одно из них - пустое")
        result = False

    return result

# нормализация chat_id - превращает в int если это на самом деле число
def normalize_chat_id(value):
    """
    Если value — строка с числом (в т.ч. отрицательным) → вернуть int.
    Если value — URL или иная строка → вернуть как есть.
    Если value — уже число → вернуть как int.
    """
    if isinstance(value, int):
        return value

    if isinstance(value, str):
        v = value.strip()
        if v.lstrip("-").isdigit():
            return int(v)
        return v

    return value

# основная запускаемая функция
async def main():

    try:

        if use_proxy:
            proxy = {
                # обязательный протокол 
                'proxy_type': proxy_type, 
                # обязательный IP-адрес прокси
                'addr': proxy_addr,
                # обязательный номер порта прокси-сервера
                'port': proxy_port,
                # имя пользователя, если прокси требует авторизации (необязательно)
                'username': proxy_username,
                # пароль, если прокси-сервер требует авторизации (необязательно)
                'password': proxy_password,
                # использовать удаленное или локальное разрешение, по умолчанию удаленное (необязательно)
                'rdns': proxy_rdns
            }
            client = TelegramClient(api_login, api_id, api_hash, proxy=proxy)
            print("Попытались подключиться через прокси")
        else:
            client = TelegramClient(api_login, api_id, api_hash)
            print("Попытались подключиться напрямую")

        if client is None:
            print("Не удалось создать клиент")
            return
        
        # соединяемся
        await client.connect()
        if not await client.is_user_authorized():
            # Handle sign-in flow
            # pass
            return
    
        client.add_event_handler(on_new_message, events.NewMessage)
        # client.add_event_handler(
        #     partial(on_new_message, extra_data=my_data),
        #     events.NewMessage
        # )
            
        # Получение информации о себе
        me = await client.get_me()

        # `me` - это пользовательский объект. Можно красиво напечатать 
        # любой объект Telegram с помощью метода `.stringify`:
        # print(me.stringify())

        # Можно получить доступ ко всем атрибутам объектов Telegram с помощью
        # оператора точки. Например, чтобы получить имя пользователя:
        username = me.username
        print(username)
        print(me.phone)

        # запуск prepoll и polling логики
        loop = asyncio.get_running_loop()
        run_pre_poll_and_reply_polling(client, loop)

        async with client:
            print("Ожидание сообщений...")
            await client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\nExiting...")
    except asyncio.CancelledError:
        print("\nTask cancelled - cleaning up...")
    finally:
        await client.disconnect()
        print("Final!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # This prevents the KeyboardInterrupt traceback from being shown
        print("\nExited cleanly")
    except Exception as e:
        print(f"Error: {e}")