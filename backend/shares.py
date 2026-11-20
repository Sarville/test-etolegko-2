def distribute(offers):
    result = [dict(offer) for offer in offers]
    active = [offer for offer in result if not offer['removed']]
    locked = [offer for offer in active if offer['locked']]
    unlocked = [offer for offer in active if not offer['locked']]
    remaining = 100 - sum(offer['share'] for offer in locked)
    if remaining < 0 or (active and not unlocked and remaining != 0):
        raise ValueError('Закреплённые доли не позволяют распределить 100%.')
    for offer in result:
        if offer['removed']:
            offer['share'] = 0
    if unlocked:
        base, extra = divmod(remaining, len(unlocked))
        for index, offer in enumerate(unlocked):
            offer['share'] = base + (index < extra)
    return result
