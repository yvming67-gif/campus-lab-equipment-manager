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
