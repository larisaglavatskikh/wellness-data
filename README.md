# wellness-data

Приватный репозиторий, куда автоматически стекаются данные WHOOP и умных весов (через Apple Health).
Claude раз в день читает `data/` и обновляет дашборд.

```
WHOOP API ──(GitHub Actions, 3 раза в день)──► data/whoop/daily.json, workouts.json, body.json
Весы Xiaomi ─► Apple Health ─► Команда iOS ──► data/health/scale.json
```

## Данные

| Файл | Что внутри |
|---|---|
| `data/whoop/daily.json` | по дням: recovery, HRV, пульс покоя, SpO2, t° кожи, strain, ккал за день, шаги, сон (часы, глубокий, REM, performance, время отбоя/подъёма) |
| `data/whoop/workouts.json` | тренировки: вид, длительность, strain, ккал, пульс, минуты в зонах 0–5 |
| `data/whoop/body.json` | рост, вес и макс. пульс из профиля WHOOP |
| `data/health/scale.json` | по дням: вес, % жира, сухая масса (самое раннее взвешивание дня — основное) |

День WHOOP привязан к дате пробуждения: сон с ночи 28→29 и recovery утра 29-го лежат в `2026-09-29`.

---

## Настройка (один раз)

### 1. Приложение WHOOP для API
1. Открыть **developer-dashboard.whoop.com**, войти своим аккаунтом WHOOP.
2. Создать Team (любое имя), затем **Create App**:
   - Name: `Wellness sync`
   - Scopes: `read:recovery`, `read:cycles`, `read:sleep`, `read:workout`, `read:body_measurement`
   - Redirect URI: `https://localhost/whoop-callback`
3. Скопировать **Client ID** и **Client Secret**.

### 2. Секреты в GitHub
Репозиторий → **Settings → Secrets and variables → Actions → New repository secret**:
- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`

### 3. Авторизация WHOOP
1. Открыть ссылку (подставить свой Client ID):
   ```
   https://api.prod.whoop.com/oauth/oauth2/auth?response_type=code&client_id=ВАШ_CLIENT_ID&redirect_uri=https%3A%2F%2Flocalhost%2Fwhoop-callback&scope=offline%20read%3Arecovery%20read%3Acycles%20read%3Asleep%20read%3Aworkout%20read%3Abody_measurement&state=wellness
   ```
2. Разрешить доступ. Браузер покажет «не удаётся открыть страницу» — так и должно быть.
3. Из адресной строки скопировать значение между `code=` и `&`.
4. **Actions → WHOOP authorize (one-time) → Run workflow**, вставить код, Run. Код живёт ~10 минут.
5. Через минуту в `data/whoop/` появится история за 180 дней. Дальше синхронизация идёт сама.

Refresh-токен WHOOP меняется при каждом обновлении; он хранится зашифрованным (ключ — ваш Client Secret) в `secrets/whoop_rt.enc`.

### 4. Токен GitHub для iPhone
**github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
- Repository access: **Only select repositories → wellness-data**
- Permissions → Repository → **Contents: Read and write**
- Expiration: максимальный срок
- Скопировать токен (`github_pat_...`) — он понадобится в Команде.

### 5. Команда iOS «Весы → GitHub»
Приложение **Команды → + → новая команда**, добавить действия по порядку:

1. **Найти образцы здоровья** (Find Health Samples): тип **Вес**; дата начала — за последние **3 дня**; сортировка по дате начала, **сначала новые**; ограничение **1**.
2. **Получить сведения об образцах здоровья → Значение** → **Задать переменную** `weight`.
3. **Получить сведения об образцах здоровья** (от результата шага 1) **→ Дата начала** → **Форматировать дату**: свой формат `yyyy-MM-dd` → переменная `date`.
4. Ещё раз **Форматировать дату** от той же даты начала: формат `HH:mm` → переменная `time`.
5. Повторить шаги 1–2 для типа **Процент жира** → переменная `fat`.
6. Повторить шаги 1–2 для типа **Сухая масса тела** → переменная `lean` (если весы её не пишут — пропустить).
7. **Получить содержимое URL** (Get Contents of URL):
   - URL: `https://api.github.com/repos/ВАШ_ЛОГИН/wellness-data/dispatches`
   - Метод: **POST**
   - Заголовки: `Authorization` = `Bearer github_pat_...`; `Accept` = `application/vnd.github+json`
   - Тело запроса: **JSON**
     - `event_type` (текст) = `health`
     - `client_payload` (словарь):
       - `date` = переменная date · `time` = time · `weight` = weight · `body_fat` = fat · `lean_mass` = lean

Запустить вручную: в **Actions → Apple Health ingest** должен появиться зелёный запуск, а в `data/health/scale.json` — запись.

**Автоматизация:** Команды → Автоматизация → **+** → **Приложение** → Mi Fitness (или Zepp Life) → **Закрыто** → **Запускать сразу** → выбрать команду.
Сценарий: взвесились → открыли приложение весов, дождались синхронизации → закрыли → данные улетели.
Можно добавить вторую автоматизацию «Время суток, 10:00» как страховку — повторы не дублируются.

---

## Если что-то сломалось
- **WHOOP sync красный, ошибка 400/401 на token** — токен потерян или отозван: повторить шаг 3.
- **Health ingest не запускается** — проверить токен GitHub (срок, доступ к репозиторию, Contents: write) и URL в Команде.
- Перезабрать историю WHOOP: **Actions → WHOOP sync → Run workflow**, `days` = нужное число.
