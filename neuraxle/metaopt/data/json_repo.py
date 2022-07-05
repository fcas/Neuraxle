"""
Neuraxle's JSON Hyperparameter Repository Classes
========================================================
Data objects and related repositories used by AutoML, SQL version.

Classes are splitted like this for the AutoML:
- Projects
- Clients
- Rounds (runs)
- Trials
- TrialSplits
- MetricResults

..
    Copyright 2022, Neuraxio Inc.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.


"""
import json
import os
from copy import deepcopy
from typing import List

from neuraxle.logging.logging import NeuraxleLogger
from neuraxle.metaopt.data.vanilla import (BaseDataclass, dataclass_2_id_attr,
    DataclassHasListMixin, from_json, HyperparamsRepository, RootDataclass, ScopedLocation,
    SubDataclassT, to_json)

ON_DISK_DELIM: str = "_"


class _OnDiskRepositoryLoggerHandlerMixin:
    """
    Mixin to add a disk logging handler to a repository. It has a cache_folder.
    """

    def __init__(self, cache_folder: str):
        self.cache_folder = cache_folder

    def add_logging_handler(self, logger: NeuraxleLogger, scope: ScopedLocation) -> 'HyperparamsRepository':
        """
        Adds an on-disk logging handler to the repository.
        The file at this scope can be retrieved with the method :func:`get_scoped_logger_path`.
        """
        logging_file = self.get_scoped_logger_path(scope)
        os.makedirs(os.path.dirname(logging_file), exist_ok=True)
        logger.with_file_handler(logging_file)
        return self

    def get_log_from_logging_handler(self, logger: NeuraxleLogger, scope: ScopedLocation) -> str:
        return ''.join(logger.read_log_file())

    def get_folder_at_scope(self, scope: ScopedLocation) -> str:
        _scope_attrs = scope.as_list(stringify=True)
        _scope_attrs = [ON_DISK_DELIM + s for s in _scope_attrs]
        return os.path.join(self.cache_folder, *_scope_attrs)

    def get_scoped_logger_path(self, scope: ScopedLocation) -> str:
        scoped_path: str = self.get_folder_at_scope(scope)
        return os.path.join(scoped_path, 'log.txt')


class HyperparamsOnDiskRepository(_OnDiskRepositoryLoggerHandlerMixin, HyperparamsRepository):
    """
    Hyperparams repository that saves json files for every AutoML trial.

    .. seealso::
        :class:`AutoML`,
        :class:`Trainer`,
    """

    def __init__(self, cache_folder: str = None):
        HyperparamsRepository.__init__(self)
        _OnDiskRepositoryLoggerHandlerMixin.__init__(self, cache_folder=cache_folder)
        self._save_dc(RootDataclass(), scope=ScopedLocation(), deep=True)

    def load(self, scope: ScopedLocation, deep=False) -> SubDataclassT:
        """
        Get metadata from scope.

        The fetched metadata will be the one that is the last item
        that is not a None in the provided scope.

        :param scope: scope to get metadata from.
        :return: metadata from scope.
        """
        try:
            loaded = self._load_dc(scope=scope, deep=deep)
        except KeyError:
            try:
                loaded: BaseDataclass = scope.new_dataclass_from_id()
            except Exception as err:
                raise err from err
        return loaded

    def save(self, _dataclass: SubDataclassT, scope: ScopedLocation, deep=False) -> 'HyperparamsRepository':
        """
        Save metadata to scope.

        :param metadata: metadata to save.
        :param scope: scope to save metadata to.
        :param deep: if True, save metadata's sublocations recursively so as to update.
        """
        self._save_dc(_dataclass=_dataclass, scope=scope, deep=deep)
        return self

    def _load_dc(self, scope: ScopedLocation, deep=False) -> SubDataclassT:
        scope, _, load_file = self._get_dataclass_filename_path(None, scope)

        _json_loaded = self._load_json_file(load_file)

        _dataclass: SubDataclassT = from_json(_json_loaded)
        if _dataclass.has_sublocation_dataclasses():
            _dataclass = self._load_dc_sublocation_keys(_dataclass, scope)
            if deep is True:
                for sub_dc_id in _dataclass.get_sublocation_keys():
                    sub_dc = self._load_dc(scope=scope.with_id(sub_dc_id), deep=deep)
                    _dataclass.store(sub_dc)
        return _dataclass

    def _load_json_file(self, load_file: str):
        if not os.path.exists(load_file):
            raise KeyError(f"{load_file} not found.")

        try:
            with open(load_file, 'r') as f:
                _file_content: str = f.read()
            return json.loads(_file_content)
        except json.decoder.JSONDecodeError as e:
            # TODO: for trials only, use UUID mechanism that could be added to the dataclass or aggregate to resolve collisions. Or investigate and fix locking.
            surrounding_files = os.listdir(os.path.dirname(load_file))
            raise ValueError(
                f"Invalid JSON file: {repr(_file_content)} in path {load_file} with folder ls={surrounding_files}."
            ) from e

    def _load_dc_sublocation_keys(self, _dataclass: SubDataclassT, scope) -> SubDataclassT:
        dc_folder: str = self.get_folder_at_scope(scope)
        sublocs: List[str] = os.listdir(dc_folder)
        # TODO: loaded keys are simply sorted. That is a problem and doesn't respect the dataclass' OrderedDict (e.g.: metrics' sorting).
        sublocs = list(sorted(
            [s[len(ON_DISK_DELIM):] for s in sublocs if s.startswith(ON_DISK_DELIM)]
        ))
        if isinstance(_dataclass, DataclassHasListMixin):
            sublocs = [int(i) for i in sublocs]
        try:
            _dataclass.set_sublocation_keys(sublocs)
        except AssertionError as e:
            raise e from e

        return _dataclass

    def _save_dc(self, _dataclass: SubDataclassT, scope: ScopedLocation, deep=False):
        scope, save_folder, save_file = self._get_dataclass_filename_path(_dataclass, scope)

        os.makedirs(save_folder, exist_ok=True)
        with open(save_file, 'w') as f:
            json.dump(to_json(_dataclass.empty()), f, indent=4)

        if deep is True and _dataclass.has_sublocation_dataclasses():
            for sub_dc in _dataclass.get_sublocation_values():
                self._save_dc(sub_dc, scope=scope, deep=deep)

    def _get_dataclass_filename_path(self, _dataclass: SubDataclassT, scope: ScopedLocation):
        scope = self._patch_scope_for_dataclass(_dataclass, scope)

        save_folder = self.get_folder_at_scope(scope)
        save_file = os.path.join(save_folder, 'metadata.json')

        return scope, save_folder, save_file

    def _patch_scope_for_dataclass(self, _dataclass: BaseDataclass, scope: ScopedLocation):
        scope = deepcopy(scope)
        if _dataclass is not None and _dataclass.get_id() is not None:
            scope = scope.at_dc(_dataclass)
            setattr(scope, dataclass_2_id_attr[_dataclass.__class__], _dataclass.get_id())
        return scope

    def is_locking_required(self) -> bool:
        return True
