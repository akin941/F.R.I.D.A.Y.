from rest_framework import serializers

from proactive import timing
from proactive.models import ProactiveNotice, ProactiveSchedule


class ScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProactiveSchedule
        fields = [
            "id",
            "kind",
            "enabled",
            "local_time",
            "weekdays",
            "speak_on_open",
            "interval_minutes",
            "quiet_start",
            "quiet_end",
            "last_run_at",
            "next_run_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ScheduleWriteSerializer(serializers.Serializer):
    """The owner's schedule editor. Strict: unknown keys are refused."""

    enabled = serializers.BooleanField()
    local_time = serializers.TimeField(required=False, allow_null=True, format="%H:%M")
    weekdays = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=timing.ALL_WEEKDAYS
    )
    speak_on_open = serializers.BooleanField(required=False, allow_null=True)
    interval_minutes = serializers.ChoiceField(
        choices=timing.HEARTBEAT_INTERVALS, required=False, allow_null=True
    )
    quiet_start = serializers.TimeField(required=False, allow_null=True)
    quiet_end = serializers.TimeField(required=False, allow_null=True)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields) - {"workspace"}
        if unknown:
            raise serializers.ValidationError({key: "Unknown field." for key in sorted(unknown)})
        return attrs


class NoticeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProactiveNotice
        fields = [
            "id",
            "kind",
            "title",
            "count",
            "link",
            "data_class",
            "created_at",
            "read_at",
            "dismissed_at",
        ]
        read_only_fields = fields
