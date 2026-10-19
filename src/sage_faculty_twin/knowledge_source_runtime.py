"""Public/private business branches scheduled by the persistent SAGE runtime."""
from contextvars import ContextVar

from sage.runtime.cancellation import bind_request_cancellation, child_request_cancellation
from sage.runtime.flownet import bootstrap_runtime, build_runtime_client, flow, stateless

from .request_context import register_request_cancel_callback, raise_if_request_cancelled

_stores = ContextVar('twin_knowledge_source_stores')


@stateless(uri='stateless://twin/knowledge-sources/wiki', namespace='twin')
def read_wiki(request):
    wiki, _ = _stores.get()
    hits = wiki.search(request['query'], top_k=request['limit'], visitor_profile='general_visitor')
    return 'wiki', hits


@stateless(uri='stateless://twin/knowledge-sources/private', namespace='twin')
def read_private(request):
    _, private = _stores.get()
    hits = private.search(request['query'], top_k=request['limit'],
                          visitor_profile=request['visitor_profile'], admin_role=request['admin_role'])
    return 'private', hits


@stateless(uri='stateless://twin/knowledge-sources/wiki-requested', namespace='twin')
def wiki_requested(request):
    return request['wiki']


@stateless(uri='stateless://twin/knowledge-sources/private-requested', namespace='twin')
def private_requested(request):
    return request['private']


@flow(uri='flow://twin/knowledge-sources', namespace='twin')
def knowledge_sources_flow(init_stream):
    return init_stream.filter(wiki_requested, operator_key='wiki-capability').map(
        read_wiki, operator_key='read-wiki').connect(
        init_stream.filter(private_requested, operator_key='private-capability').map(
            read_private, operator_key='read-private'))


class KnowledgeSourceRuntime:
    def __init__(self):
        self._bootstrap = bootstrap_runtime(node_mode='local')
        try:
            client = build_runtime_client(owner='twin', runtime_host=self._bootstrap.runtime_host)
            self._endpoint = knowledge_sources_flow.bind('twin-sources-in', 'twin-sources-out').endpoint(
                client=client, reuse_existing=False)
        except BaseException:
            self._bootstrap.shutdown()
            raise

    def search(self, *, wiki, private, request, timeout):
        # Snapshot the adapter's capability decision before native scheduling.
        request = dict(request)
        if any(type(request.get(origin)) is not bool for origin in ('wiki', 'private')):
            raise ValueError('source_branch_requires_boolean_capability')
        expected = {origin for origin in ('wiki', 'private') if request[origin]}
        if not expected:
            raise ValueError('source_branch_requires_enabled_origin')
        cancellation = child_request_cancellation()
        unregister = register_request_cancel_callback(cancellation.cancel)
        token = _stores.set((wiki, private))
        try:
            raise_if_request_cancelled()
            with bind_request_cancellation(cancellation):
                outputs = self._endpoint.call(request, timeout=timeout)
        finally:
            _stores.reset(token)
            unregister()
        results = {}
        for origin, hits in outputs:
            if origin not in expected or origin in results:
                raise RuntimeError('invalid_knowledge_source_branch_output')
            results[origin] = hits
        if set(results) != expected:
            raise RuntimeError('incomplete_knowledge_source_branch_outputs')
        return results

    def close(self):
        self._endpoint.close()
        self._bootstrap.shutdown()
