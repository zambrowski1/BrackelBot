# Матрица этапов 2 и 3

Фактический результат прогона: verification-stage23.txt. Локальные имитации
сохранения не означают, что выполнена реальная правка в Википедии.

| Сценарий | Автоматическая проверка |
|---|---|
| Трансфер и возвращение | test_transfer_atomic_preserves_formatting, test_return_to_previous_club_is_a_new_stint |
| Аренда и резерв | test_loan_reserve_keep_parent_active |
| Неизвестные годы / незакрытый клуб | test_unknown_years_refused, test_unclosed_previous_stint_refused |
| Основная и возрастные сборные | test_add_team_age_separate, test_create_national_career_missing_or_empty |
| Новый сезон и итоги | test_new_season_rowspans_totals, test_recompute_confirmed_known_totals |
| Неполная статистика | test_unknown_season_values_never_filled_zero |
| Еврокубки и новая колонка | test_new_european_competition_existing_category, test_structure_proposal_never_automatically_executed |
| Несовпадение старых чисел и групповой откат | test_multi_row_group_stats_old_mismatch, test_transfer_failure_rolls_back_everything |
| Верные/неверные ссылки Borussia | test_borussia_identity_exact_qid_and_live_page, test_borussia_ambiguous_or_wrong_object_refused |
| Возраст, флаг, иностранный клуб | test_national_age_and_flag_checked, test_foreign_club_verified_from_qid_and_country |
| Повторные операции | test_repeat_transfer_idempotent, новый сезон/сборная, прежняя регрессия repeat apply |
| Индивидуальные подтверждения и атомарная запись | test_manual_publication_one_save_atomic_group, test_partial_group_approval_never_writes |
| Ревизия / подмена плана | test_revision_or_text_change_requires_new_approval, test_tampered_plan_never_writes |
| Запрет основного пространства | test_publisher_hard_whitelist_before_any_network, test_wiki_client_write_hard_gate |
| Dry Run / Automatic | test_no_write_outside_manual |
| Login, CSRF, baserevid, assertuser | test_botpassword_session_no_credentials_on_disk, test_action_edit_baserevid_assertions_and_csrf |
| Защищённая страница, потеря входа, конфликт | test_api_edit_errors_no_retry |
| Неизвестный результат записи | test_edit_timeout_ambiguous_never_retried |
| Резервная копия, восстановление | test_restore_review_revision_and_backup, test_restore_invalid_title_or_hash_refused |
| Ошибки GUI и смена режима | test_error_decision_and_confirmation_disabled, test_manual_mode_confirmations_invalidated_on_mode_change |
| Все сгенерированные примеры | test_all_generated_scenarios_against_full_fixture |

Прежние тесты этапа 1 сохранены; тест запрета публикации адаптирован к новой
реализации Manual. Поддержка неизвестной структуры проверяется отказом, а не
считается успешной реализацией произвольных таблиц.

Отдельно выполнены GET-запросы для идентичности мёнхенгладбахской Боруссии,
основной сборной Германии и U21. Статистические источники не запрашивались.
Живой login/edit/restore пока не испытывался. Ни один тест не отправляет POST
в Википедию; используются Mock и MemoryClient.
