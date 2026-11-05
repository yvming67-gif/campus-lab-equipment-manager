from django import template
from core.domain_labels import (NOTIFICATION_LABELS, channel_label, event_label,
                                notification_label, work_state_label)

register = template.Library()
LABELS = NOTIFICATION_LABELS
register.filter("business_label", notification_label)
register.filter("event_label", event_label)
register.filter("work_state_label", work_state_label)
register.filter("channel_label", channel_label)
business_label = notification_label


@register.filter
def resource_icon(resource):
    """A closed set of own SVG symbols; no user-provided markup or paths."""
    if resource.kind == 'ROOM':
        return 'icon-room'
    label=(resource.name+' '+resource.code+' '+resource.equipment.category).lower()
    for needles,icon in [(['摄影','相机','cam'],'camera'),(['机器人','robot','rob'],'robot'),
                         (['计算机','笔记本','laptop','lap'],'laptop'),(['传感','sensor','sen'],'sensor')]:
        if any(word in label for word in needles):
            return 'resource-'+icon
    return 'icon-equipment'
