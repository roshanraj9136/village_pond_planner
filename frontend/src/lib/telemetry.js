// OpenTelemetry in the browser: one span per user action ("find pond site", "save site"),
// with a child span for every API call made during it (fetch instrumentation). The fetch
// instrumentation also adds a W3C `traceparent` header to those calls, so the gateway and
// the worker continue the same trace: one trace from the click to the database.
//
// Spans go to /otel/v1/traces on the same gateway (same origin, so no CORS), which hands
// them to the OpenTelemetry Collector. If the gateway has that route switched off, the
// export fails quietly and the app is not affected.
import { context, SpanStatusCode, trace } from '@opentelemetry/api'
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http'
import { registerInstrumentations } from '@opentelemetry/instrumentation'
import { FetchInstrumentation } from '@opentelemetry/instrumentation-fetch'
import { resourceFromAttributes } from '@opentelemetry/resources'
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base'
import { WebTracerProvider } from '@opentelemetry/sdk-trace-web'

const provider = new WebTracerProvider({
  resource: resourceFromAttributes({
    'service.name': 'pond-frontend',
    'service.namespace': 'jaldrishti',
    'service.version': '3.0.0',
  }),
  spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter({ url: '/otel/v1/traces' }), { scheduledDelayMillis: 2000 })],
})
provider.register()

registerInstrumentations({
  instrumentations: [
    new FetchInstrumentation({
      // our own API only: connection probes, span uploads and the place search would be noise
      ignoreUrls: [/\/gateway\/health/, /\/otel\/v1\/traces/, /nominatim\.openstreetmap\.org/],
      clearTimingResources: true,
    }),
  ],
})

const tracer = trace.getTracer('jaldrishti-frontend')

// The browser has no async context: after an `await` the active span is lost. So the span
// of the action in progress is kept here and tracedFetch() puts it back around fetch().
let actionContext = null

/** Runs `fn` as one traced user action; the API calls it makes become child spans. */
export async function userAction(name, attributes, fn) {
  const span = tracer.startSpan(name, { attributes })
  const ctx = trace.setSpan(context.active(), span)
  actionContext = ctx
  try {
    return await fn()
  } catch (err) {
    if (err?.name !== 'AbortError') {
      span.recordException(err)
      span.setStatus({ code: SpanStatusCode.ERROR, message: err?.message })
    }
    throw err
  } finally {
    if (actionContext === ctx) actionContext = null
    span.end()
  }
}

/** fetch() inside the current user action, so its span (and traceparent) has the right parent. */
export function tracedFetch(url, options) {
  return actionContext ? context.with(actionContext, () => fetch(url, options)) : fetch(url, options)
}
