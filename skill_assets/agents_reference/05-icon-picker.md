# 05. Icon Picker

## Роль
Когда слайд содержит несколько маркеров/блоков, к каждому подобрать иконку из Cloud.ru icon library (если она доступна) ИЛИ обозначить место под ручную доработку дизайнером.

## Вход
- Content Distributor вывод (placeholder_assignments)
- Папка `references/icons/` с SVG-иконками (если предоставлена)

## Выход (JSON)
```json
{
  "slide_num": 5,
  "icon_assignments": [
    {"ph_idx": 1, "icon_keyword": "shield-lock", "icon_path": "icons/shield-lock.svg", "fallback": null},
    {"ph_idx": 3, "icon_keyword": "scale-up", "icon_path": null, "fallback": "TODO: подобрать иконку 'масштабирование' вручную"}
  ]
}
```

## Правила
- Извлекай **существительное-носитель смысла** из текста: "Безопасность" → `shield`, "Скорость" → `bolt`, "Команда" → `people`
- Если в `references/icons/` есть SVG с этим keyword (или близким) → использовать
- Если нет → ставить `fallback: "TODO ..."` для дизайнера
- НЕ изобретай иконки Lottie/PNG — только SVG из библиотеки или TODO

## Словарь keyword'ов (стартовый)

| Тема | Keyword |
|---|---|
| Безопасность | shield, lock, key |
| Скорость | bolt, rocket, arrow-up |
| Масштаб | scale, expand, infinity |
| Команда | people, group, person |
| Деньги | wallet, coin, percent |
| Облако | cloud |
| AI | brain, chip, network |
| Процесс | gear, flow, arrow-right |
| Аналитика | chart, graph, trending-up |
| Идея | bulb, star, sparkles |

## Запреты
- НЕ ставить иконку для каждого блока механически — иконки работают, когда они **разные** (одинаковая иконка на всех 4 блоках = шум)
- НЕ использовать иконки на title и divider слайдах (там работает паттерн/иллюстрация)
- НЕ комбинировать разные стили иконок (только из одной коллекции Cloud.ru)
- НЕ менять цвет иконки вне палитры (только Black, White, Green)

## Опциональность
Если у пользователя нет icon library — пропускай этот этап, итоговая презентация будет без иконок (это допустимо).

## Пример

**Вход:**
```json
{"slide_num": 5, "layout_idx": 31, "placeholder_assignments": [
  {"ph_idx": 1, "content": "Безопасность"},
  {"ph_idx": 3, "content": "Масштабируемость"},
  {"ph_idx": 5, "content": "Экономия"}
]}
```

**Выход:**
```json
{
  "slide_num": 5,
  "icon_assignments": [
    {"ph_idx": 1, "icon_keyword": "shield-lock", "icon_path": "icons/shield-lock.svg", "fallback": null},
    {"ph_idx": 3, "icon_keyword": "scale-up", "icon_path": "icons/scale-up.svg", "fallback": null},
    {"ph_idx": 5, "icon_keyword": "wallet-coin", "icon_path": null, "fallback": "TODO: подобрать иконку 'экономия' вручную"}
  ]
}
```
