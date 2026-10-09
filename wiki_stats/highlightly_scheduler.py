# SPDX-License-Identifier: MIT
"""Monitor explicitly bound players. Never infer departures from a partial list."""
from uuid import uuid4
from .api_football_client import utcnow
from .errors import UpdateError
from .player_registry import PlayerRegistry
from .scheduler import CycleRunner, review
from .state_store import StoreAudit
from .statistics_engine import normalize_dataset


class HighlightlyCycleRunner(CycleRunner):
    def run(self):
        with self.store.run_lock():
            run_id = uuid4().hex
            log = StoreAudit(self.store, run_id)
            report = {'run_id': run_id, 'started_at': utcnow(), 'mode': self.mode, 'provider': 'highlightly',
                      'coverage': 'registered_players_only', 'league': 78, 'season': self.season,
                      'api_available': False, 'status': 'running', 'player_count': 0, 'article_count': 0,
                      'discrepancies': 0, 'prepared_count': 0, 'published_count': 0, 'error_count': 0,
                      'review_count': 0, 'departures': 0, 'plans': [], 'errors': []}
            try:
                teams = self.api.scope(self.season)
                report['api_available'] = True
                self.store.put('scope', str(self.season), {'league': 78, 'teams': list(teams.values()), 'fetched_at': utcnow()})
                registry = PlayerRegistry(self.store)
                ids = sorted(int(k) for k, p in self.store.items('players') if p.get('monitoring') == 'active')
                cursor = self.store.get('api_checkpoint', str(self.season), {}).get('next_id', 0)
                order = [pid for pid in ids if pid >= cursor] + [pid for pid in ids if pid < cursor]
                if not ids:
                    review(self.store, 'identity', 'highlightly', 'registered_players_missing')
                    report['review_count'] += 1
                for index, pid in enumerate(order):
                    self.store.put('api_checkpoint', str(self.season), {'next_id': pid, 'complete': False})
                    try:
                        dataset = self.api.player(pid, self.season, fresh=False)
                        players, observations = normalize_dataset(dataset, expected_season=self.season)
                        report['player_count'] += 1
                        team = self.api.current_team(pid, self.season)
                        # This membership attests only this player's current profile.
                        for obs in observations:
                            self._process(obs, players, teams, {pid: {team['id']}}, registry, report, log)
                    except UpdateError as exc:
                        if exc.code in {'api_quota_exhausted', 'api_rate_limited', 'api_authentication', 'api_unavailable'}: raise
                        review(self.store, 'statistics', pid, exc.code)
                        report['review_count'] += 1
                    next_id = order[index + 1] if index + 1 < len(order) else 0
                    self.store.put('api_checkpoint', str(self.season), {'next_id': next_id, 'complete': index + 1 == len(order)})
                if self.mode == 'automatic' and not report.get('publication_blocked'):
                    self._publish_automatic(report, log)
                report['status'] = 'blocked' if report.get('publication_blocked') else 'completed'
            except UpdateError as exc:
                report['status'] = 'stopped'
                report['error_count'] += 1
                report['errors'].append({'status': exc.code, 'message': str(exc)})
            except Exception:
                report['status'] = 'failed'
                report['error_count'] += 1
                report['errors'].append({'status': 'internal_error', 'message': 'Ошибка обработки Highlightly; публикация остановлена.'})
            finally:
                report['finished_at'] = utcnow()
                report['requests'] = self.api.used
                report['quotas'] = self.api.quota_status()
                self.store.put('runs', run_id, report)
                log.log('cycle_finished', status=report['status'], published=report['published_count'])
            return report
