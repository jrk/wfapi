# -*- coding: utf-8 -*-
"""Provide Workflowy access

>>> Workflowy("")

"""

import re
import json
import threading
from contextlib import contextmanager
from urllib.error import HTTPError # TODO: remove this
from .browser import DefaultBrowser
from .internal import get_globals_from_home
from ..project.quota import *
from ..const import DEFAULT_WORKFLOWY_CLIENT_VERSION, DEFAULT_WORKFLOWY_URL
from ..transaction import ClientTransaction, ServerTransaction, \
    SimpleSubClientTransaction
from ..node.manager import NodeManager, NodeManagerInterface
from ..operation import OperationCollection
from ..error import *
from ..utils import *

__all__ = ["BaseWorkflowy", "Workflowy"]


class BaseWorkflowy(NodeManagerInterface):
    CLIENT_TRANSACTION_CLASS = NotImplemented
    SERVER_TRANSACTION_CLASS = NotImplemented
    CLIENT_SUBTRANSACTION_CLASS = NotImplemented
    DEFAULT_BROWSER_CLASS = NotImplemented
    NODE_MANAGER_CLASS = NotImplemented
    
    def __init__(self):
        raise NotImplementedError

    def transaction(self):
        raise NotImplementedError

    # smart handler?
    # TODO: change handle method
    @contextmanager
    def smart_handle_init(self):
        try:
            yield
            self.handle_init()
        finally:
            pass

    @contextmanager
    def smart_handle_reset(self):
        try:
            self.handle_reset()
            yield
        finally:
            pass

    def handle_init(self):
        pass

    def handle_reset(self):
        pass

    def handle_logout(self, counter=0):
        pass

    def reset(self):
        # TODO: give argument to _reset and smart handler?
        with self.smart_handle_reset():
            self._reset()

    def init(self, *args, **kwargs):
        # TODO: give argument to smart handler? (_init require argument!)
        with self.smart_handle_init():
            self._init(*args, **kwargs)

    @property
    def inited(self):
        return self._inited

    @inited.setter
    def inited(self, inited):
        if inited:
            self._inited = True
        else:
            if self._inited:
                self.reset()
            
            self._inited = False


class Workflowy(BaseWorkflowy, OperationCollection):
    CLIENT_TRANSACTION_CLASS = ClientTransaction
    SERVER_TRANSACTION_CLASS = ServerTransaction
    CLIENT_SUBTRANSACTION_CLASS = SimpleSubClientTransaction
    DEFAULT_BROWSER_CLASS = DefaultBrowser
    NODE_MANAGER_CLASS = NodeManager

    client_version = DEFAULT_WORKFLOWY_CLIENT_VERSION

    def __init__(self, share_id=None, *, sessionid=None, username=None, password=None):
        self._inited = False

        self.browser = self._init_browser()

        # TODO: replace main_project to Project
        #       but still wf.root are keep self.main_project's nodemgr's root!

        self.globals = attrdict()
        self.settings = attrdict()
        self.project_tree = attrdict()
        self.main_project = attrdict()
        #self.projects = None
        
        # TODO: status are owned by main_project, but that's not good! remove soon.
        self.status = attrdict()
        
        # TODO: will be removed from Workflowy, but moved to Project (eg. shared view)
        self.current_transaction = None
        self.transaction_lock = threading.RLock()

        # TODO: will be removed from Workflowy, but moved to Project
        self.nodemgr = self.NODE_MANAGER_CLASS()
        
        # TODO: will be removed from Workflowy, but moved to Project
        self.quota = VoidQuota()

        if sessionid is not None or username is not None:
            username_or_sessionid = sessionid or username
            self.login(username_or_sessionid, password)

        self.init(share_id)

    def _reset(self):
        self.handle_reset()
        self.browser.reset()

        self.globals.clear()
        self.settings.clear()
        self.project_tree.clear()
        self.main_project.clear()
        self.status.clear()

        self.current_transaction = None

        self.nodemgr.clear()
        self.quota = Quota()

    def print_status(self):
        rvar = dict(
            globals=self.globals,
            settings=self.settings,
            project_tree=self.project_tree,
            main_project=self.main_project,
            quota=self.quota,
        )

        pprint(vars(self), width=240)

    @classmethod
    def _init_browser(cls):
        return cls.DEFAULT_BROWSER_CLASS(DEFAULT_WORKFLOWY_URL)

    def _login_failed(self):
        self.inited = False
        raise WFLoginError("Login Failure.")

    def handle_logout(self, counter=0):
        self._handle_logout(self, counter)

    def transaction(self, *, force_new_transaction=False):
        # TODO: how to handle force_new_transaction?
        # TODO: how to split handle for projects?
        with self.transaction_lock:
            if self.current_transaction is None:
                self.current_transaction = self.CLIENT_TRANSACTION_CLASS(self)
            else:
                return self.CLIENT_SUBTRANSACTION_CLASS(self, self.current_transaction)

            return self.current_transaction

    def login(self, username_or_sessionid, password=None, *, auto_init=True, use_ajax_login=True):
        home_content = None

        if password is None:
            session_id = username_or_sessionid
            self.browser.set_cookie("sessionid", session_id)
        else:
            username = username_or_sessionid
            if use_ajax_login:
                res, data = self.browser["ajax_login"](username=username, password=password)
                errors = data.get("errors")
                if errors:
                    # 'errors' or 'success'
                    self._login_failed()
            else:
                res, data = self.browser["accounts/login/"](username=username, password=password, next="", _raw=True)
                home_content = data

        if auto_init:
            return self.init(home_content=home_content)

    def _get_initialization_data(self, share_id=None):
        url = "get_initialization_data"
        info = dict(
            client_version=self.client_version,
        )

        if share_id is not None:
            info.update(share_id=share_id)

        try:
            res, data = self.browser["get_initialization_data"](_query=info)
        except HTTPError as e:
            if e.code == 404:
                raise WFLoginError
            
            raise
        
        return data
        
    def _init(self, share_id=None, *, home_content=None):
        login_counter = 0
        while login_counter < 3:
            try:
                data = self._get_initialization_data(share_id=share_id)
            except WFLoginError as e:
                self.handle_logout(login_counter)
                login_counter += 1
            else:
                break
        else:
            self._login_failed()

        if home_content is None:
            _, home_content = self.browser[""](_raw=True)
        self.globals.update(get_globals_from_home(home_content))

        data = attrdict(data)
        self.globals.update(data.globals)
        self.settings.update(data.settings)
        # TODO: support auxiliaryProjectTreeInfos for embbed node.
        # self.projects.import_sub_project?
        self.project_tree.steal(data, "projectTreeData")
        self.main_project.steal(self.project_tree, "mainProjectTreeInfo")
        # self.projects.import_main_project?
        # self.projects = self.PROJECT_MANAGER_CLASS(self.project_tree?, OR
        #    mainProjectTreeInfo, auxiliaryProjectTreeInfos?)
        
        self._status_update_by_main_project()
        self.nodemgr.update_root(
            root_project=self.main_project.pop("rootProject"),
            root_project_children=self.main_project.pop("rootProjectChildren"),
        )
        
        self.handle_init()
        self.inited = True

    @property
    def root(self):
        return self.nodemgr.root

    def _status_update_by_main_project(self):
        status = self.status
        mp = self.main_project

        status.most_recent_operation_transaction_id = mp.initialMostRecentOperationTransactionId
        status.date_joined_timestamp_in_seconds = mp.dateJoinedTimestampInSeconds
        status.polling_interval = mp.initialPollingIntervalInMs / 1000
        status.is_readonly = mp.isReadOnly

        if mp.get("shareType"):
            status.share_type = mp.shareType
            status.share_id = mp.shareId
        else:
            status.share_type = None
            status.share_id = None

        # main_project also contains overQuota if shared.
        status.is_shared_quota = "overQuota" in mp

        if status.is_shared_quota:
            self.quota = SharedQuota()
            status.is_over_quota = mp.overQuota
        else:
            self.quota = DefaultQuota()
            status.items_created_in_current_month = mp.itemsCreatedInCurrentMonth
            status.monthly_item_quota = mp.monthlyItemQuota

        self._quota_update()

    def _quota_update(self):
        status = self.status
        quota = self.quota

        if status.is_shared_quota:
            quota.is_over = status.is_over_quota
        else:
            quota.used = status.items_created_in_current_month
            quota.total = status.monthly_item_quota

    def __contains__(self, node):
        return node in self.nodemgr

    def __getitem__(self, node):
        return self.nodemgr[node]

    def __iter__(self):
        return iter(self.nodemgr)

    def add_node(self, node, recursion=True, update_quota=True):
        added_nodes = self.nodemgr.add(node, recursion=recursion)

        if update_quota:
            self.quota += added_nodes

    def remove_node(self, node, recursion=False, update_quota=True):
        removed_nodes = self.nodemgr.remove(node, recursion=recursion)

        if update_quota:
            self.quota -= removed_nodes

    @property
    def pretty_print(self):
        return self.nodemgr.pretty_print

    def _refresh_project_tree(self):
        nodes = self.nodes
        main_project = self.main_project
        root_project = self.root_project

        # TODO: refreshing project must keep old node if uuid are same.
        # TODO: must check root are shared. (share_id and share_type will help us.)

        raise NotImplementedError

    def execute_transaction(self, tr):
        push_poll_info = self._execute_client_transaction(tr)

        self._handle_errors_by_push_poll(push_poll_info)
        for data in self._status_update_by_push_poll(push_poll_info):
            self._execute_server_transaction(tr, data)

    def _execute_client_transaction(self, tr):
        data = tr.get_transaction_json()
        arguments = dict (
            client_id=self.project_tree.clientId,
            client_version=self.client_version,
            push_poll_id=tr.tid,
            push_poll_data=json.dumps(data),
        )

        if self.status.share_type is not None:
            # how to merge code with ClientTransaction.get_transaction_json()
            assert self.status.share_type == "url"
            arguments.update(share_id=self.status.share_id)

        res, data = self.browser["push_and_poll"](**arguments)
        return data

    def _handle_errors_by_push_poll(self, data):
        error = data.get("error")
        if error:
            raise WFRuntimeError(error)

        logged_out = data.get("logged_out")
        if logged_out:
            raise WFLoginError("logout detected, don't share session with real user.")

    def _status_update_by_push_poll(self, data):
        results = data.get("results")
        if results is None:
            # TODO: raise error?
            return

        with debug_helper_with_json(data):
            for res in results:
                res = attrdict(res)
                yield self._status_update_by_push_poll_sub(res)

    def _status_update_by_push_poll_sub(self, res):
        error = res.get("error")
        if error:
            raise WFRuntimeError(error)

        status = self.status
        status.most_recent_operation_transaction_id = \
            res.new_most_recent_operation_transaction_id

        if res.get("need_refreshed_project_tree"):
            raise NotImplementedError
            self._refresh_project_tree()
            # XXX how to execute operation after refresh project tree? no idea.

        status.polling_interval = res.new_polling_interval_in_ms / 1000

        if status.is_shared_quota:
            status.is_over_quota = res.over_quota
        else:
            status.items_created_in_current_month = \
                res.items_created_in_current_month
            status.monthly_item_quota = res.monthly_item_quota

        self._quota_update()

        data = json.loads(res.server_run_operation_transaction_json)
        return data

    def _execute_server_transaction(self, tr, data):
        transaction = self.SERVER_TRANSACTION_CLASS.from_server_operations(self, tr, data)
        transaction.commit()

