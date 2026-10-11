/**
 * MongoDB persistence for graphs.
 *
 * One document per graph (`_id` = graph id) holding nodes/edges/positions plus a monotonically
 * increasing `revision` and `updatedAt`. The repository is expressed as an interface so tests can
 * run against either a real (in-memory) MongoDB or a trivial fake without touching route code.
 */
import type { Graph } from '@lng/graph-contract';
import type { Collection, Db, MongoClient } from 'mongodb';

export interface StoredGraph {
  graph: Graph;
  revision: number;
  updatedAt: string;
}

export interface GraphRepository {
  read(graphId: string): Promise<StoredGraph>;
  save(graphId: string, graph: Graph, options?: { name?: string }): Promise<StoredGraph>;
  ping(): Promise<boolean>;
  close(): Promise<void>;
}

interface GraphDocument {
  _id: string;
  name?: string;
  nodes: Graph['nodes'];
  edges: Graph['edges'];
  revision: number;
  updatedAt: Date;
}

export class MongoGraphRepository implements GraphRepository {
  constructor(private readonly client: MongoClient, private readonly db: Db) {}

  private collection(): Collection<GraphDocument> {
    return this.db.collection<GraphDocument>('graphs');
  }

  async read(graphId: string): Promise<StoredGraph> {
    const document = await this.collection().findOne({ _id: graphId });
    if (!document) {
      return { graph: { id: graphId, name: 'Untitled graph', nodes: [], edges: [] }, revision: 0, updatedAt: new Date().toISOString() };
    }
    return {
      graph: { id: document._id, name: document.name ?? undefined, nodes: document.nodes ?? [], edges: document.edges ?? [] },
      revision: document.revision ?? 0,
      updatedAt: (document.updatedAt ?? new Date()).toISOString(),
    };
  }

  /** Saves and atomically bumps the revision; creates the document on first save. */
  async save(graphId: string, graph: Graph, options: { name?: string } = {}): Promise<StoredGraph> {
    const now = new Date();
    const document = await this.collection().findOneAndUpdate(
      { _id: graphId },
      {
        $set: {
          name: options.name ?? graph.name,
          nodes: graph.nodes ?? [],
          edges: graph.edges ?? [],
          updatedAt: now,
        },
        $inc: { revision: 1 },
        $setOnInsert: { _id: graphId },
      },
      { upsert: true, returnDocument: 'after' },
    );

    if (!document) {
      throw new Error('failed to persist graph');
    }
    return {
      graph: { id: graphId, name: document.name ?? undefined, nodes: document.nodes ?? [], edges: document.edges ?? [] },
      revision: document.revision ?? 1,
      updatedAt: (document.updatedAt ?? now).toISOString(),
    };
  }

  async ping(): Promise<boolean> {
    try {
      await this.db.command({ ping: 1 });
      return true;
    } catch {
      return false;
    }
  }

  async close(): Promise<void> {
    await this.client.close();
  }
}

/** In-memory repository used by tests and as a degradation path when MongoDB is unreachable. */
export class MemoryGraphRepository implements GraphRepository {
  private readonly store = new Map<string, StoredGraph>();

  async read(graphId: string): Promise<StoredGraph> {
    const existing = this.store.get(graphId);
    if (existing) return { ...existing, graph: structuredClone(existing.graph) };
    return { graph: { id: graphId, name: 'Untitled graph', nodes: [], edges: [] }, revision: 0, updatedAt: new Date().toISOString() };
  }

  async save(graphId: string, graph: Graph, options: { name?: string } = {}): Promise<StoredGraph> {
    const previous = this.store.get(graphId);
    const stored: StoredGraph = {
      graph: { id: graphId, name: options.name ?? graph.name, nodes: structuredClone(graph.nodes ?? []), edges: structuredClone(graph.edges ?? []) },
      revision: (previous?.revision ?? 0) + 1,
      updatedAt: new Date().toISOString(),
    };
    this.store.set(graphId, stored);
    return stored;
  }

  async ping(): Promise<boolean> {
    return true;
  }

  async close(): Promise<void> {
    this.store.clear();
  }
}
