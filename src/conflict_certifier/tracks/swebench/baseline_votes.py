"""Offline vote aggregation and operational metrics; no model calls."""
from collections import Counter

from conflict_certifier.evaluation import EvaluationEvidence, evaluate_predictions, result_record


def aggregate(identifier, votes, split):
    k = len(votes)
    if not k:
        raise ValueError('at least one vote required')
    # A label reached without any executed command (note=no_evidence) is kept for audit
    # but never counts as a vote.
    counts = Counter(v.get('baseline_label') for v in votes if v.get('note') != 'no_evidence')
    label = next((x for x in ('CONFLICTING', 'NOT_CONFLICTING')
                  if counts[x] > k / 2), 'INCONCLUSIVE')
    evidence = (EvaluationEvidence.inconclusive('vote_abstention', 'No strict majority of all votes')
                if label == 'INCONCLUSIVE' else
                EvaluationEvidence.complete([label == 'NOT_CONFLICTING']))
    if all(v.get('status') == 'SKIPPED' for v in votes):
        evidence = EvaluationEvidence.error('votes_unavailable', 'All investigations failed before evaluation')
    result = result_record('instance_id', identifier,
                           evaluate_predictions(evidence, [split == 'original']),
                           baseline_label=label, evidence_tier='llm_baseline' if label != 'INCONCLUSIVE' else None)
    result['vote_counts'] = {
        'conflicting': counts['CONFLICTING'],
        'not_conflicting': counts['NOT_CONFLICTING'],
        'inconclusive': sum(v.get('status') != 'SKIPPED' and
                            (v.get('note') == 'no_evidence' or
                             v.get('baseline_label') not in ('CONFLICTING', 'NOT_CONFLICTING')) for v in votes),
        'skipped': sum(v.get('status') == 'SKIPPED' for v in votes),
    }
    result['k'] = k
    result['conflict_score'] = counts['CONFLICTING'] / k
    return result


def confusion_metrics(counts):
    tp, tn, fp, fn = (counts[k] for k in ('tp','tn','fp','fn'))
    def pct(a, b):
        return round(100*a/b, 4) if b else None
    return dict(counts=counts, percent=dict(
        accuracy=pct(tp+tn, tp+tn+fp+fn),
        false_positive=pct(fp, fp+tn), false_negative=pct(fn, fn+tp),
        precision=pct(tp,tp+fp), recall=pct(tp,tp+fn), f1=pct(2*tp,2*tp+fp+fn)))


def policy_metrics(results, split):
    """All task denominators; unavailable (including skipped) obeys both policies.

    Also usable on SpecGuard records: predictions determine the binary decision,
    independently of whether the full vector exactly matches ground truth.
    """
    if split not in ('original', 'oneoff', 'conflicting'):
        raise ValueError(split)
    n = len(results)
    labels = []
    for r in results:
        p = r.get('predictions')
        labels.append(None if not p else ('conflict' if False in p else 'clean'))
    c = Counter(labels)
    broken = split != 'original'
    counts = dict(covered=c['conflict']+c['clean'], unavailable=c[None],
                  skipped=sum(r.get('status') == 'SKIPPED' for r in results),
                  false_negative=c['clean'] if broken else None,
                  false_positive=c['conflict'] if not broken else None,
                  fail_open_miss=c['clean']+c[None] if broken else None,
                  fail_closed_false_block=c['conflict']+c[None] if not broken else None,
                  correct=c['conflict'] if broken else c['clean'])
    percent = {k: round(v/n*100, 4) if v is not None and n else None
               for k,v in counts.items()}
    percent['coverage'] = percent.pop('covered')
    percent['accuracy_all_tasks'] = percent.pop('correct')
    policies = {}
    for name, block_unknown in [('fail_open', False), ('fail_closed', True)]:
        blocked = c['conflict'] + (c[None] if block_unknown else 0)
        allowed = n-blocked
        policies[name] = confusion_metrics(dict(tp=blocked if broken else 0,
            fn=allowed if broken else 0, fp=blocked if not broken else 0,
            tn=allowed if not broken else 0))
    return dict(total_tasks=n, counts=counts, percent=percent, policies=policies,
                unavailable_policy='Includes inconclusive and infrastructure/skipped results; never silently excluded.')
