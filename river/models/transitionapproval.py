import logging

from django.db.models import CASCADE
from mptt.fields import TreeOneToOneField

from river.models import State, TransitionApprovalMeta
from river.utils.error_code import ErrorCode
from river.utils.exceptions import RiverException

try:
    from django.contrib.contenttypes.fields import GenericForeignKey
except ImportError:
    from django.contrib.contenttypes.generic import GenericForeignKey

from django.db import models, transaction
from django.utils.translation import ugettext_lazy as _

from river.models.base_model import BaseModel
from river.models.managers.transitionapproval import TransitionApprovalManager
from river.config import app_config

__author__ = 'ahmetdal'

PENDING = 0
APPROVED = 1
REJECTED = 2

PROCEEDING_STATUSES = [
    (PENDING, _('Pending')),
    (APPROVED, _('Approved')),
    (REJECTED, _('Rejected')),
]

LOGGER = logging.getLogger(__name__)


class TransitionApproval(BaseModel):
    class Meta:
        app_label = 'river'
        verbose_name = _("Transition Approval")
        verbose_name_plural = _("Transition Approvals")

    objects = TransitionApprovalManager()

    content_type = models.ForeignKey(app_config.CONTENT_TYPE_CLASS, verbose_name=_('Content Type'), on_delete=CASCADE)
    field_name = models.CharField(_("Field Name"), max_length=200)

    object_id = models.CharField(max_length=50, verbose_name=_('Related Object'))
    workflow_object = GenericForeignKey('content_type', 'object_id')

    meta = models.ForeignKey(TransitionApprovalMeta, verbose_name=_('Meta'), related_name="proceedings", on_delete=CASCADE)
    source_state = models.ForeignKey(State, verbose_name=_("Source State"), related_name='transition_approvals_as_source', on_delete=CASCADE)
    destination_state = models.ForeignKey(State, verbose_name=_("Next State"), related_name='transition_approvals_as_destination', on_delete=CASCADE)

    transactioner = models.ForeignKey(app_config.USER_CLASS, verbose_name=_('Transactioner'), null=True, blank=True, on_delete=CASCADE)
    transaction_date = models.DateTimeField(null=True, blank=True)

    status = models.IntegerField(_('Status'), choices=PROCEEDING_STATUSES, default=PENDING)

    skipped = models.BooleanField(_('Skip'), default=False)

    permissions = models.ManyToManyField(app_config.PERMISSION_CLASS, verbose_name=_('Permissions'))
    groups = models.ManyToManyField(app_config.GROUP_CLASS, verbose_name=_('Groups'))
    priority = models.IntegerField(default=0, verbose_name=_('Priority'))

    enabled = models.BooleanField(_('Enabled?'), default=True)

    previous = TreeOneToOneField("self", verbose_name=_('Previous Transition'), related_name="next_transition", null=True, blank=True, on_delete=CASCADE)

    cloned = models.BooleanField(_('Cloned?'), default=False)

    skipped_from = models.ManyToManyField("self", verbose_name=_("Skipped from"), related_name='created_after_skipped', null=True, blank=True)

    @transaction.atomic
    def skip(self):
        if self.skipped:
            LOGGER.info("TransitionApproval with id %s is already skipped.")
            return

        self.skipped = True
        self.save()

        if self._can_skip_whole_step:
            for skipped_approval in self._all_skipped_at_same_layer:
                for downstream_approval in self._downstream:
                    transition_approval, created = TransitionApproval.objects.update_or_create(
                        workflow_object=self.workflow_object,
                        field_name=self.field_name,
                        source_state=skipped_approval.source_state,
                        destination_state=downstream_approval.destination_state,
                        priority=self.priority,
                        meta=self.meta,
                        transactioner=downstream_approval.transactioner,
                        status=PENDING
                    )
                    transition_approval.skipped_from.add(self)
                    transition_approval.permissions.add(*downstream_approval.permissions.all())
                    transition_approval.groups.add(*downstream_approval.groups.all())
        self._downstream.update(skipped=True)

    @property
    def _downstream(self):
        return TransitionApproval.objects.filter(
            workflow_object=self.workflow_object,
            field_name=self.field_name,
            source_state=self.destination_state,
            skipped=False
        )

    @property
    def _all_skipped_at_same_layer(self):
        return TransitionApproval.objects.filter(
            workflow_object=self.workflow_object,
            field_name=self.field_name,
            source_state=self.source_state,
            destination_state=self.destination_state,
            skipped=True,
        )

    @property
    def _can_skip_whole_step(self):
        return TransitionApproval.objects.filter(
            workflow_object=self.workflow_object,
            field_name=self.field_name,
            source_state=self.source_state,
            destination_state=self.destination_state,
            skipped=False
        ).exclude(pk=self.pk).count() == 0
