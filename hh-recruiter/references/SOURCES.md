# Основания комплекта

Подготовлено 22.09.2026. Текущая версия профиля — 1.0. Рубрика хранения — hermes-hh-1.0-rubric3.

## Требования пользователя и прежний проект

- Пользователь просит создать HH-рекрутера в Hermes без Codex.
- Затем пользователь потребовал участие Telegram-бота и явно выбрал режим «Только для вас»: управление подбором из личного чата владельца, без интервью кандидатов в Telegram.
- Исходный проект: https://github.com/ztmlipetsk-sys/hh-ai-recruiter-agent (закрытый репозиторий пользователя; прочитаны README.md, AGENTS.md, criteria.py, hh_client.py, cli.py в main).
- Веса из criteria.py, blob SHA 62c241d3a47e7d8866daf4adcf0ad60e23464a52: 25/20/20/15/10/5/5, предел 59 без production.
- Последний прочитанный отчёт пользователя: «Вставленная ​​уценка(1).md», сформирован 18.09.2026 09:08 МСК. В нём: 196 обработанных откликов, 183 пригодные уникальные оценки, 13 резюме без оценки из-за лимита 16 000 символов, 16 первоначальных вопросников, 1 ответ. Это исторический снимок. Действующий статус HH в этом сеансе не проверялся.
- Реальная база, резюме, переписка, ключи и доступ к Windows-компьютеру в комплект не перенесены. Старый мониторинг не выключен этим пакетом. Перед включением нового расписания требуется сверка.
- Подход к детализации баллов в SOUL.md предложен для Hermes; сохранены веса и пороги, но совпадение баллов со старым оценщиком не гарантируется. Поэтому версия хранения выделена отдельно.

## Официальные технические источники

1. Hermes Desktop / Bot Mode: https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode
2. Hermes, экспорт и импорт профиля: https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions#export-and-import-a-profile-file
3. Hermes, инструменты: https://hermes-agent.nousresearch.com/docs/user-guide/features/tools
4. Hermes, конфигурация и переменные окружения: https://hermes-agent.nousresearch.com/docs/user-guide/configuration
5. Hermes, провайдеры/запуск: https://hermes-agent.nousresearch.com/docs/getting-started/quickstart
6. HH API и регистрация приложения: https://dev.hh.ru/
7. HH OpenAPI: https://api.hh.ru/openapi/redoc
8. Официальная модель коллекций откликов: https://github.com/hhru/api/blob/master/docs/employer_negotiations.md
9. Hermes Telegram: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram

Платные методы работодателя и поиск по базе зависят от подключённой услуги и прав. Наличие доступа к базе резюме и наличие прав на работу с откликами — разные проверки. Цена услуги и наличие доступа у пользователя не установлены.

Макет архива соответствует функции import_profile из официального hermes_cli/profiles.py (прочитан blob SHA 5b7e1f5bbe96a598fd9568315193d21270f4b708): единственный корневой каталог hh-recruiter/, содержащий профиль. Настоящий запуск Hermes и живой HH API требуют пользовательской среды.
