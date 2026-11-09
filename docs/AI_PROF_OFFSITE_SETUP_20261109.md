# AI PROF — независимое SFTP-резервирование

## Что готово

Система формирует **два** зашифрованных пакета, отдельные от MARINA SMART и KÖL:

- control-plane-YYYYMMDDTHHMMSSZ.tar.gz.gpg — очередь, журналы, статусы GitHub/Telegram;
- n8n-ai-prof-YYYY-MM-DD_HHMMSS.tar.gz.gpg — PostgreSQL n8n, config, volumes.

Оба пакета имеют SHA256. Перед отправкой выполняется локальное восстановление. После загрузки по SFTP каждый файл **скачивается обратно** и сверяется SHA256. Удалённый каталог обязан существовать заранее.

## Для активации требуется независимый сервер

Необходимы: точный SFTP host, user, port, заранее созданная папка только для AI PROF, установленный на сервере публичный SSH-ключ, проверенный host fingerprint. Настройки сохраняются в `~/.config/ai-prof-control-center/offsite-sftp.json` с правами 0600, исходный шаблон `ops/offsite-sftp.example.json`.

Проверка перед реальным запуском:
```bash
python3 scripts/offsite_control_plane_sftp.py --check-only
```

Запуск и доказательство загрузки с чтением обратно:
```bash
bash scripts/run_ai_prof_offsite_20261109.sh
```

Только после PASS включить ежедневное расписание (04:00 по Бишкеку):
```bash
mkdir -p ~/.config/systemd/user
cp ops/systemd/user/ai-prof-offsite-sftp.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-prof-offsite-sftp.timer
```

**Таймер НЕ активирован без сервера.** Не использовать парольные интерактивные входы, отключение проверки host key, произвольный общий хостинг без отдельных прав или логирование секретов.

## Отдельный ключ восстановления

Локальный ключ расшифровки находится на Ubuntu и **не отправляется в SFTP**. Его вторую защищённую копию нужно сохранить отдельно от Ubuntu и отдельно от SFTP-архивов. Полная аварийная готовность подтверждается только после извлечения и расшифровки пакетов с независимого хранилища в изолированной среде. Без этого offsite = NOT VERIFIED.
