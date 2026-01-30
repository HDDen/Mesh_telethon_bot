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
import hashlib
from functools import partial
from typing import Any, Dict, Optional, Iterable, List, Union
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
            "http_extpoll_token": "", # токен для получения сообщений и prepoll
            "http_send_token": "", # токен для отправки телеграмм-сообщений в меш через php-бэкенд, должен совпадать с TG_SUBSCRIBE_TOKEN со стороны бэкенда. Его можно указать в самой ссылке send_updates_to в виде get-параметра, в целях совместимости с апдейтом от телеграм, а здесь - не указывать. Его область действия - только на отправку из телеграм в меш
            "prepoll_url": [ # эти коллбэки выполняются перед запуском скрипта
                # "", ""
            ], 
            "send_updates_to": "https://...?token=aaaaaa", # куда сгружать полученные от TG обновления, https://... Токен можно передать не в get-параметре, указав его в самой ссылке, а в JSON-теле. Отправляется POST с телом {"token": "...", "message": {"text": "...", "message_id": "...", chat: {"id": "..."}, "from": {"username": "...", "first_name": "...", "last_name": "..."}, "date": ...unix}} 
            "poll_replies_from": "", # откуда забирать ответные сообщения, отправляемые в TG https://... Ожидается ответ вида {"messages":[{"name":"Alice","date":"18.01 19:44","msg":"Foo","chat_id": "-10055555555"},{"name":"Bob","date":"18.01 19:44","msg":"Bar","chat_id": "-10055555555"}]}
            "poll_period_seconds": 30, # период, с которым опрашивается poll_replies_from
            "http_ignore_ssl_errors": False, 
        },
    ],
    "sent_to_tg_cache_ttl": 300, # возможна ситуация, в которой одно сообщение может быть отправлено в тг несколькими воркерами, таким образом задвоив его. Чтобы этого не произошло, используется кэш _sent_to_tg_messages_cache, хранящий хэши отправленных сообщений. Настройка sent_to_tg_cache_ttl указывает, сколько времени каждый хэш остаётся актуальным
    "sent_to_tg_cache_key_elems": ["chat_id", "msg"], # из каких свойств сообщения составлять хэш
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
sent_to_tg_cache_ttl = int(_config.get("sent_to_tg_cache_ttl", DEFAULT_CONFIG["sent_to_tg_cache_ttl"]))
sent_to_tg_cache_key_elems = _config.get("sent_to_tg_cache_key_elems", DEFAULT_CONFIG["sent_to_tg_cache_key_elems"])

# кэш отправленных сообщений из poll в tg для дедупликации
_sent_to_tg_messages_cache: Dict[str, float] = {}
_sent_to_tg_messages_cache_lock = asyncio.Lock()

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
                if worker_chat_id == str(getattr(chat, "id", None)):
                    worker_params = worker_cfg
                elif worker_chat_id == str(getattr(chat, "username", None)):
                    worker_params = worker_cfg
                    chat.id = str(chat.username)
                elif worker_chat_id == "@"+str(getattr(chat, "username", None)):
                    worker_params = worker_cfg
                    chat.id = "@"+str(getattr(chat, "username", None))
                elif worker_chat_id == "-100"+str(getattr(chat, "id", None)):
                    chat.id = "-100"+str(chat.id)
                    worker_params = worker_cfg

            except Exception as e:
                print(f"Произошла ошибка: {e}")
                # tb = traceback.format_exc()
                # print(tb)
                break

    # собираем данные
    if worker_params:
        msg_text = ""

        msg_media = getattr(msg, "media", None)
        msg_file = getattr(msg, "file", None)
        msg_photo = getattr(msg, "photo", None)
        
        if getattr(msg_media, "video", None):
            msg_text = "Отправлено видео"
            duration = getattr(msg_file, "duration", None)
            if duration:
                msg_text = msg_text + " длит. " + str(int(duration)) + " сек."
        elif getattr(msg_media, "voice", None):
            msg_text = "Отправлен войс"
            duration = getattr(msg_file, "duration", None)
            if duration:
                msg_text = msg_text + " длит. " + str(int(duration)) + " сек."
        elif msg_photo:
            msg_text = "Отправлена картинка"

        # добавляем текст - в виде подписи или самостоятельно
        if getattr(msg, "text", None):
            if msg_text:
                msg_text = msg_text + " с подписью: "+msg.text
            else:
                msg_text = msg.text

        data = {
            "message": {
                "text": msg_text,
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

async def on_edited_message(event):
    await on_new_message(event)

# отправка json с апдейтом во внешнюю систему
def send_to_extmsngr(url, payload, worker_params):
    
    result = False

    verify_ssl = not bool(worker_params.get("http_ignore_ssl_errors", False))
    http_send_token = worker_params.get("http_send_token", "")

    if not url:
        print("send_to_extmsngr(): url не задан - отправка во внешнюю систему пропущена")
        return result
    
    # добавим токен в post
    payload["token"] = http_send_token

    # и отправим запрос
    try:
        print("Отправка сообщения во внешнюю систему: \n", protect_dict_values(payload, ["token"], "***"))
        resp = do_post_request(url, payload, 10, verify_ssl)
        if resp:
            print("Сообщение успешно отправлено во внешнюю систему.\n")
            result = True
        else:
            print("Неуспешная отправка сообщения в внешнюю систему resp=", resp)
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
        http_extpoll_token = worker_cfg.get("http_extpoll_token")

        prepoll_urls = worker_cfg.get("prepoll_url")
        if isinstance(prepoll_urls, list):
            print("Запуск prepoll_url...")
            for url in prepoll_urls:
                if url:
                    try:
                        print(f"PREPOLL запрос: {url}")
                        # выполняем запрос
                        payload = {
                            "token": http_extpoll_token
                        }
                        resp = do_post_request(url, payload, 10, verify_ssl)
                        if resp:
                            if isinstance(resp, dict):
                                print("HTTP pre-poll JSON ответ:\n%s", json.dumps(resp, ensure_ascii=False, indent=2))
                            else:
                                print("HTTP pre-poll ответ не является JSON: %s", resp)
                        else:
                            print("HTTP pre-poll запрос неудачный: resp=", resp)
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
                        # выполняем запрос
                        payload = {
                            "token": http_extpoll_token
                        }
                        resp = do_post_request(url, payload, 10, verify_ssl)
                        if resp:
                            if isinstance(resp, dict):

                                # пример: преобразование сообщений в словари
                                messages = resp.get("messages", [])
                                if messages:
                                    print("Получены данные:", resp)

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
                                print("Ответ получен, но это не json", resp)
                        else:
                            print(f"Polling завершился с ошибкой")
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

    # вычислим хэш сообщения чтобы понять, нужно ли отправлять его в telegram
    already_sent = False
    msg_key = _make_message_key(msg_dict)

    # проверка в кэше
    async with _sent_to_tg_messages_cache_lock:
        await _cleanup_sent_to_tg_messages_cache()

        if msg_key in _sent_to_tg_messages_cache:
            # Сообщение уже отправлялось
            already_sent = True

        # резервируем ключ заранее, чтобы другие корутины не отправили дубль
        _sent_to_tg_messages_cache[msg_key] = time.time()

    # проверка прошла, уведомим пользователя
    if not already_sent:

        # извлечем текст сообщения
        # msg_dict представляет собой объект {'date': '25.01 14:30', 'msg': 'Текст сообщения', 'chat_id': '-10055555555555'}
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
    else:
        print("send_to_telegram(): already_sent = True, уже отправляли сообщение, пропускаем")
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

# заменяет значения переданных ключей в плоском объекте на плейсхолдер, полезно для последующего вывода в лог
def protect_dict_values(src_dict: dict, keys_list: list, placeholder: str = "***"):

    for_log = src_dict.copy()

    if keys_list:
        for index, item in enumerate(keys_list):
            for_log[item] = '***'
    
    return for_log

# выполняет POST-запрос, отправляет переданный json, при успехе возвращает ответ в виде dict
# При ошибке возвращает None
def do_post_request(url: str, payload: Optional[dict] = None, timeout: int = 10, verify_ssl: bool = True) -> Optional[Union[dict, str]]:

    result = None

    if payload is None:
        payload = {}

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=timeout,
            verify=verify_ssl
        )
        resp.raise_for_status() # проверка if resp.status_code == 200: не нужна, raise_for_status() выбрасывает исключение requests.exceptions.HTTPError, если статус-код 4xx или 5xx (ошибка клиента или сервера).

        try:
            result = resp.json()
        except ValueError:
            print("do_post_request(): ответ не является JSON: %s", resp.text[:500])
            result = resp.text

    except Exception as e:
        print(f"do_post_request(): POST завершился с ошибкой или таймаутом: \n{e}")
        result = None

    return result

# возвращает хэш сообщения из chat_id, даты и текста
def _make_message_key(msg_dict: dict) -> str:
    
    try:
        if sent_to_tg_cache_key_elems:
            fields = sent_to_tg_cache_key_elems
        else:
            fields = ['chat_id', 'msg']
            print(f"sent_to_tg_cache_key_elems пуст, сбросили до дефолтного \n{fields}")

        raw = "|".join(str(msg_dict.get(field, "")) for field in fields)
        normalized = " ".join(raw.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    except Exception as e:
        print(f"_make_message_key(): ошибка", e)
        return ""

# функция для поиска и удаления устаревших хэшей из кэша отправленных в tg сообщений
async def _cleanup_sent_to_tg_messages_cache():
    now = time.time()
    expired_keys = [
        key for key, ts in _sent_to_tg_messages_cache.items()
        if now - ts > sent_to_tg_cache_ttl
    ]
    for key in expired_keys:
        del _sent_to_tg_messages_cache[key]

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
        await client.start()
        await client.connect()
        if not await client.is_user_authorized():
            # Handle sign-in flow
            # pass
            return
    
        client.add_event_handler(on_new_message, events.NewMessage)
        client.add_event_handler(on_edited_message, events.MessageEdited)
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