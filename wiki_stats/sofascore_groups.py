"""Keep verified card fields atomic and optional tables independent."""
from copy import deepcopy

PREREQUISITES = {'bind_infobox_name', 'migrate_career_template'}


def partition(article):
    prerequisites = {}; groups = {}
    for op in article['operations']:
        if op['type'] in PREREQUISITES:
            key = op['type']
            prerequisites.setdefault(key, op)
            continue
        target = op.get('target', {})
        previous = op.get('group_id','')
        if target.get('structure') in {'career', 'infobox'} or op['type'] in {'update_shirt_number', 'update_current_club'}:
            national = target.get('field') == 'национальная сборная' or op.get('payload', {}).get('field') == 'обновление данных о сборной'
            group = (previous if previous.startswith('sofascore-card-national') else 'sofascore-card-national-'+previous) if national else 'sofascore-card-club'
        elif 'sbmatch' in op['type']:
            group = 'sofascore-national-table'
        else:
            group = previous if previous.startswith('dfb-club-') else 'sofascore-club-table'
        op['group_id'] = group
        groups.setdefault(group, []).append(op)
    if not groups and prerequisites: groups['sofascore-card-club'] = []
    ordered = []
    for group, operations in groups.items():
        for index, op in enumerate(prerequisites.values()):
            shared = deepcopy(op)
            shared.update(id=f'shared-infobox-{index}-{group}', group_id=group)
            ordered.append(shared)
        ordered.extend(operations)
    article['operations'] = ordered


def plan(snapshot, article, resolver):
    from .change_planner import plan_article
    partition(article)
    return plan_article(snapshot, article, '1.1', resolver)
