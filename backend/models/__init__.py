# -*- coding: utf-8 -*-
"""模型统一注册入口（供 create_all / 迁移使用）。"""
from backend.models.app_setting import AppSetting
from backend.models.base import Base
from backend.models.backup_record import BackupRecord
from backend.models.export_template import ExportTemplate
from backend.models.export_template_link import ExportTemplateProject
from backend.models.import_batch import ImportBatch
from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint
from backend.models.project import Project
from backend.models.warning_event import WarningEvent
from backend.models.warning_rule import WarningRule
from backend.models.warning_rule_set import WarningRuleSet, WarningRuleSetProject

__all__ = ["Base", "Project", "MeasurementPoint", "ObservationRecord", "ImportBatch", "WarningRule", "WarningEvent", "BackupRecord", "ExportTemplate", "ExportTemplateProject", "WarningRuleSet", "WarningRuleSetProject", "AppSetting"]
