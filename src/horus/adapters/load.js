import http from 'k6/http';
import { Rate } from 'k6/metrics';

const input = JSON.parse(__ENV.HORUS_K6_INPUT);
const dataFailure = new Rate('horus_data_failure');
const httpFailure = new Rate('horus_http_failure');
let sampleCount = 0;

export const options = {
  scenarios: { load: {
    executor: 'shared-iterations', vus: input.profile.vus,
    iterations: input.profile.iterations,
    maxDuration: input.profile.maxDurationMs + 'ms', gracefulStop: '0s',
  } },
  maxRedirects: 0,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)'],
};

function pointer(value, path) {
  if (path === '') return value;
  for (const part of path.slice(1).split('/')) {
    const key = part.replace(/~1/g, '/').replace(/~0/g, '~');
    if (value === null || typeof value !== 'object' || !(key in value)) return undefined;
    value = value[key];
  }
  return value;
}

function equal(a, b) {
  if (typeof a !== typeof b) return false;
  if (a === null || b === null || typeof a !== 'object') return a === b;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(k => k in b && equal(a[k], b[k]));
}

function compare(actual, assertion) {
  const expected = assertion.expected;
  switch (assertion.operator) {
    case 'exists': return (actual !== undefined) === (typeof expected === 'boolean' ? expected : true);
    case 'eq': return equal(actual, expected);
    case 'ne': return actual !== undefined && !equal(actual, expected);
    case 'length': return actual !== null && typeof actual === 'object'
      ? Object.keys(actual).length === expected
      : typeof actual === 'string' && actual.length === expected;
    case 'gte': return typeof actual === 'number' && typeof expected === 'number' && actual >= expected;
    case 'lte': return typeof actual === 'number' && typeof expected === 'number' && actual <= expected;
    case 'type': {
      const type = actual === null ? 'null' : Array.isArray(actual) ? 'array' : typeof actual;
      return expected === 'integer' ? Number.isInteger(actual) : type === expected;
    }
    default: throw new Error('Unsupported load assertion');
  }
}

export default function () {
  const statusAssertions = input.assertions.filter(a => a.status !== null);
  const allowed = statusAssertions.length
    ? (Array.isArray(statusAssertions[0].status) ? statusAssertions[0].status : [statusAssertions[0].status])
    : [200];
  const response = http.request(input.method, input.url,
    input.body === null ? null : JSON.stringify(input.body), {
      headers: input.headers, redirects: 0, timeout: '15s', tags: {horus_vu: String(__VU)},
      responseCallback: http.expectedStatuses(...allowed),
    });
  const statusFailed = !statusAssertions.every(a => Array.isArray(a.status)
    ? a.status.includes(response.status) : response.status === a.status);
  httpFailure.add(response.status === 0 || statusFailed);
  let body;
  try { body = response.json(); } catch (_) { body = response.body; }
  const dataAssertions = input.assertions.filter(a => a.status === null);
  const failed = dataAssertions.filter(a => {
    const actual = a.jsonPointer !== null ? pointer(body, a.jsonPointer)
      : Object.keys(response.headers).reduce((value, key) =>
        key.toLowerCase() === a.header.toLowerCase() ? response.headers[key] : value, undefined);
    return !compare(actual, a);
  });
  if (response.status !== 0) dataFailure.add(failed.length > 0);
  if ((statusFailed || failed.length) && sampleCount < 5) {
    sampleCount++;
    console.log('HORUS_SAMPLE:' + JSON.stringify({
      vu: __VU, iteration: __ITER, status: response.status,
      failedAssertions: failed.map(a => a.id),
      body: String(response.body).slice(0, 2048),
    }));
  }
}

export function handleSummary(data) {
  return { [__ENV.HORUS_SUMMARY_PATH]: JSON.stringify(data) };
}
