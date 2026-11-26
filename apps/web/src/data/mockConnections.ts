import type { Connection } from '../types/connection'

/**
 * Isolated mock connections dataset for the Connections module (Step 1).
 * Replace with API queries when backend connector management is implemented.
 */
export const MOCK_CONNECTIONS: Connection[] = [
  {
    id: 'conn-pg-orders',
    name: 'prod-orders-pg',
    type: 'PostgreSQL',
    status: 'Connected',
    endpoint: 'db-orders.internal:5432/orders',
    description: 'Primary transactional orders read replica',
    lastSuccessfulSync: '4 mins ago',
    syncFrequency: 'Every 15 mins',
  },
  {
    id: 'conn-mysql-crm',
    name: 'crm-mysql-read',
    type: 'MySQL',
    status: 'Connected',
    endpoint: 'crm-ro.internal:3306/accounts',
    description: 'Customer account and subscription records',
    lastSuccessfulSync: '22 mins ago',
    syncFrequency: 'Every 30 mins',
  },
  {
    id: 'conn-mongo-events',
    name: 'events-mongo',
    type: 'MongoDB',
    status: 'Connected',
    endpoint: 'atlas-cluster-01.mongodb.net/telemetry',
    lastSuccessfulSync: '11 mins ago',
    syncFrequency: 'Every 10 mins',
  },
  {
    id: 'conn-rest-billing',
    name: 'billing-rest-v2',
    type: 'REST API',
    status: 'Error',
    endpoint: 'https://api.stripe.com/v2/invoices',
    description: 'Invoice and payout webhook polling source',
    lastSuccessfulSync: '1 hr ago',
    syncFrequency: 'Hourly',
    errorMessage: 'HTTP 429 rate limit exceeded on last poll.',
  },
  {
    id: 'conn-csv-vendor',
    name: 'vendor-feed-csv',
    type: 'CSV',
    status: 'Connected',
    endpoint: 's3://partner-drops/catalog/*.csv',
    lastSuccessfulSync: '2 hrs ago',
    syncFrequency: 'Every 6 hrs',
  },
  {
    id: 'conn-json-edge',
    name: 'edge-events-json',
    type: 'JSON',
    status: 'Disconnected',
    endpoint: 'gs://stream-archive/edge-events/',
    description: 'Archived edge payload bucket (paused)',
  },
]
