Python-скрипт для трансляции сообщений в/из Telegram напрямую через учётную запись пользователя. Используется библиотека [Telethon](https://github.com/LonamiWebs/Telethon).

## Как работает?

Сперва необходимо получить api_id и api_hash от Telegram для целевого аккаунта. Переходим на [my.telegram.org](https://my.telegram.org), авторизовываемся с номером телефона. Переходим в [API development tools](https://my.telegram.org/apps). Указываем имя "приложения" (например, TgMeshTranslator), платформу указываем Web. Сохраняем, видим поля с интересующими нас значениями.

> Внимание! Разработчик другой библиотеки (MadelineProto) [рекомендует уведомлять Telegram](https://docs.madelineproto.xyz/docs/LOGIN.html#getting-permission-to-use-the-telegram-api) о том, что на Вашей учётной записи будет использоваться api-автоматизация, чтобы снизить вероятность автоматического бана. Для этого необходимо отправить письмо на **[recover@telegram.org](mailto:recover@telegram.org)** с указанием телефона и объяснением, что будет делать пользовательский бот. Например:
> 
> Hello! I'm going to use automation on my Telegram account using the Telethon python library. The account has the login <ваш @юзернейм> and the phone number <привязанный номер телефона>. I plan to set up a bridge to communicate with my friends using the Meshtastic LoRa radio network. I've tried using a bot instead, but the issue is that messages from other bots added to the conversation are not available through the bot_api, so I decided to try using the library to work directly with the account. I'm writing to your inbox because, for example, the MadelineProto documentation recommended notifying Telegram about using automation on the account to prevent false positives and account blocking. Thanks!

### После этого можно производить настройку.

При первом запуске создаётся шаблонный файл конфигурации `mesh_telethon_config.json`, который необходимо настроить под себя. Описание параметров можно взять из кода скрипта.

Далее, при перезапуске, будет произведена попытка входа в учётную запись Telegram. Понадобится указать номер телефона в формате +7XXXXXXXXXX (без пробелов), затем ввести проверочный код (поступит либо по СМС, либо в сам Telegram от сервисного аккаунта).

Затем стартует сама рабочая логика. При запуске перебираются настроенные воркеры, в каждом перед началом прослушивания выполняется предварительный HTTP-запрос (полезно, если используется облачный [php-бэкенд](https://github.com/HDDen/mesh_php-backend) – позволяет удалить старый журнал сообщений).

При получении входящего сообщения / события редактирования сообщения срабатывает коллбэк `on_new_message()`. В нём проверяется, создана ли в `mesh_telethon_config.json` конфигурация для этого `chat_id`, если создана - сообщение отправляется через HTTP-POST на бэкенд (можно использовать [облачный php-сервер](https://github.com/HDDen/mesh_php-backend), можно использовать [локальный Python-порт](https://github.com/HDDen/mesh_py-local-backend)). Url отправки из Telegram во внешнюю систему указывается в `send_updates_to`. Формат отправки схож с форматом апдейта Telegram по вебхуку:

```
{
   "token":http_send_token,
   "message":{
      "text":"...",
      "message_id":"...",
      "chat":{
         "id":"..."
      },
      "from":{
         "username":"...",
         "first_name":"...",
         "last_name":"..."
      },
      "date":...unix
   }
}
```

Чтобы забрать из внешней системы сообщения, предназначенные для трансляции в Telegram, раз в `poll_period_seconds` скрипт обращается к url из `poll_replies_from` посредством POST-запроса с JSON вида `{"token": http_extpoll_token}`. В ответ ожидаем массив сообщений в виде JSON:

```
{
   "messages":[
      {
         "name":"Alice",
         "date":"18.01 19:44",
         "msg":"Foo",
         "chat_id":"-10055555555"
      },
      {
         "name":"Bob",
         "date":"18.01 19:44",
         "msg":"Bar",
         "chat_id":"@AwesomeUsername"
      }
   ]
}
```

Этот массив обрабатывается, по chat_id из каждого сообщения проверяем наличие настроенного конфига. Если находим - отправляем сообщение с текстом из `msg`.

## Дополнительные советы

- При использовании локального python-сервера можно установить частоту обновления `poll_period_seconds` повыше, сократив задержку до 5 секунд.
  
- Для повышенной защиты сетевого соединения с Telegram поддерживается подключение через socks5-прокси. Настраивается в конфиге, секции `proxy_*` и флаг `use_proxy`.
  
- Для дедупликации транслируемых в Telegram сообщений из внешней системы используется внутренний кэш отправленных сообщений, в котором хранится хэш каждого переданного в отправку сообщения, созданный из полей сообщения, указанных в `sent_to_tg_cache_key_elems` – по-умолчанию это `chat_id+msg`. Кэш хранится в оперативной памяти, время жизни каждой записи указывается в `sent_to_tg_cache_ttl` (дефолтно 5 минут), проверка кэша запускается с каждым успешным polling-запросом, в котором мы получили сообщения.
- Переменная `http_ignore_ssl_errors` полезна, если, например, используется php-версия бэкенда, развёрнутая на локальном сервере.
- Пример настроенного конфига:

```
{
  "api_id": 123456,
  "api_hash": "45sd56f44fs5dfdfsf",
  "api_login": "My_Awesome_Login",
  "use_proxy": true,
  "proxy_type": "socks5",
  "proxy_addr": "1.2.3.4",
  "proxy_port": 4221,
  "proxy_username": "Telethon",
  "proxy_password": "d$#%sdGvvz",
  "proxy_rdns": true,
  "tg_chats_configs": [
    {
      "chat_id": "-1005555555555",
      "chat_alias_faked": "Эта переменная просто для читабельности - здесь можно подписать название конфига, например, указав название группы выше",
      "http_extpoll_token": "токен-для-prepoll_url-и-poll_replies_from",
      "http_send_token": "токен-для-send_updates_to",
      "prepoll_url": [
        "http://127.0.0.1:12440?action=extpoll_delete_messages"
      ],
      "send_updates_to": "http://127.0.0.1:12440",
      "poll_replies_from": "http://127.0.0.1:12440?action=extpoll_get_messages",
      "poll_period_seconds": 5,
      "http_ignore_ssl_errors": false
    },
    {
      "chat_id": "@rove",
      "chat_alias_faked": "Павел Дуров",
      "http_extpoll_token": "fooooooooo",
      "http_send_token": "baaaaaaaar",
      "prepoll_url": [
        "http://127.0.0.1:12440?action=extpoll_delete_messages"
      ],
      "send_updates_to": "http://127.0.0.1:12440",
      "poll_replies_from": "http://127.0.0.1:12440?action=extpoll_get_messages",
      "poll_period_seconds": 5,
      "http_ignore_ssl_errors": false
    }
  ],
  "sent_to_tg_cache_ttl": 300,
  "sent_to_tg_cache_key_elems": ["chat_id", "msg"]
}
```
