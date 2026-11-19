import test from 'node:test';
import assert from 'node:assert/strict';
import { parseRoad1, createRoadGraph } from '../site/v3/roadnet.js';
import { createWorld, createSim } from '../site/v3/skate.js';

function makeTestRoad1Buffer() {
  // Synthetic 4-node, 2-edge L-junction with a 20% Potrero-style hill on edge 0
  const nVerts = 4, nStreetIdx = 6, nRouteIdx = 0, nTiles = 1;
  const nNodes = 3, nEdges = 2, S1 = 10, nAdj = 4, nPoly = 5;
  const pad4 = (n) => n + ((4 - (n % 4)) % 4);
  const total = 44
    + pad4(nVerts * 12) + pad4(nVerts * 12) + pad4(nVerts * 16) + pad4(nVerts * 4) + pad4(nVerts * 12)
    + pad4(nTiles * 16) + pad4(nTiles * 16) + pad4(nStreetIdx * 4) + pad4(nRouteIdx * 4)
    + pad4(nNodes * 12) + pad4(nNodes * 8) + pad4(nAdj * 4)
    + pad4(nEdges * 16) + pad4(nEdges * 16) + pad4(nPoly * 16) + pad4(nPoly * 4);
  const buf = new ArrayBuffer(total);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x31444152, true);
  dv.setUint32(4, 1, true);
  dv.setUint32(8, nVerts, true);
  dv.setUint32(12, nStreetIdx, true);
  dv.setUint32(16, nRouteIdx, true);
  dv.setUint32(20, nTiles, true);
  dv.setUint32(24, nNodes, true);
  dv.setUint32(28, nEdges, true);
  dv.setUint32(32, S1, true);
  dv.setUint32(36, nAdj, true);
  dv.setUint32(40, nPoly, true);

  let off = 44;
  const put = (T, vals) => {
    const a = new T(buf, off, vals.length);
    a.set(vals);
    off += pad4(vals.length * T.BYTES_PER_ELEMENT);
  };
  put(Float32Array, [0, 10, -4, 0, 10, 4, 100, 30, -4, 100, 30, 4]); // pos
  put(Float32Array, [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]);           // nrm
  put(Float32Array, [0, -1, 1, 0, 0, 1, 1, 0, 1, -1, 1, 0.5, 1, 1, 1, 0.5]); // aInfo
  put(Float32Array, [0, 0, 2, 2]);                                   // aB
  put(Float32Array, [0, 0, -1, 0, 0, -1, 100, 0, -1, 100, 0, -1]);   // aRt
  put(Float32Array, [50, 20, 0, 70]);                                // tileBounds
  put(Uint32Array, [0, 6, 0, 0]);                                    // tileRanges
  put(Uint32Array, [0, 1, 2, 1, 3, 2]);                              // streetIdx
  put(Uint32Array, []);                                              // routeIdx
  // Graph: node 0 (0,10,0), node 1 (100,30,0), node 2 (100,30,80)
  put(Float32Array, [0, 10, 0, 100, 30, 0, 100, 30, 80]);
  put(Uint32Array, [0, 1, 1, 2, 3, 1]);                              // nodeEdgeSpan
  put(Uint32Array, [0, 0, 1, 1]);                                    // nodeAdj
  put(Uint32Array, [0, 1, 0, 3, 1, 2, 3, 2]);                        // edgeMeta
  put(Float32Array, [8.0, 100.0, 0.2, 1, 9.0, 80.0, 0.0, 2]);        // edgeProps
  // polyPts: edge 0 has 3 pts (0->50->100 rising +20%), edge 1 has 2 pts (flat along +z)
  put(Float32Array, [
    0, 10, 0, 0,
    50, 20, 0, 50,
    100, 30, 0, 100,
    100, 30, 0, 0,
    100, 30, 80, 80,
  ]);
  put(Uint32Array, [0, 1, 2, 2, 3]);
  return buf;
}

test('parseRoad1 zero-copy parses header, mesh, and graph', () => {
  const buf = makeTestRoad1Buffer();
  const { header, mesh, graph } = parseRoad1(buf);
  assert.equal(header.version, 1);
  assert.equal(header.nVerts, 4);
  assert.equal(header.nEdges, 2);
  assert.equal(header.S1, 10);
  assert.equal(mesh.streetIdx.length, 6);
  assert.equal(graph.polyPts.length, 20);
});

test('createRoadGraph roadAt returns exact 20% grade, elevation, normal, and intersection topology', () => {
  const { graph } = parseRoad1(makeTestRoad1Buffer());
  const worldRoad = createRoadGraph(graph, ['(unnamed)', '20th St', 'Kansas St'], 1.5);

  // Midpoint of 20% hill edge 0 at x=50, z=1 (1 m right of centerline)
  const q = worldRoad.roadAt(50, 1, 1, 0);
  assert.equal(q.found, true);
  assert.equal(q.edgeId, 0);
  assert.ok(Math.abs(q.grade - 0.2) < 1e-4, `expected +20% grade, got ${q.grade}`);
  assert.ok(Math.abs(q.y - 20 * 1.5) < 1e-3, `expected exaggerated y=30, got ${q.y}`);
  assert.equal(q.widthM, 8.0);
  assert.ok(q.ny > 0.9, 'surface normal should point upward');

  // Heading bias at junction (100, 0): heading +z selects edge 1 (Kansas St, flat)
  const q2 = worldRoad.roadAt(100, 2, 0, 1);
  assert.equal(q2.found, true);
  assert.equal(q2.edgeId, 1);
  assert.ok(Math.abs(q2.grade) < 1e-4);

  // Intersection topology query at node 1
  const junc = worldRoad.intersectionAt(1);
  assert.equal(junc.id, 1);
  assert.deepEqual(junc.edges, [0, 1]);

  // Nearest road snap from off-road point (50, 25)
  const snap = worldRoad.nearestRoad(50, 25, 100);
  assert.ok(snap);
  assert.ok(Math.abs(snap.x - 50) < 1e-3);
  assert.ok(Math.abs(snap.z - 0) < 1e-3);
});

test('skate.js createWorld uses worldRoad directly for smooth hill traversal', () => {
  const { graph } = parseRoad1(makeTestRoad1Buffer());
  const worldRoad = createRoadGraph(graph, ['(unnamed)', '20th St', 'Kansas St'], 1.5);
  const world = createWorld({ terrainH: { at: () => 0 }, EX: 1.5, G: null, col: null, worldRoad });
  assert.equal(world.hasStreets, true);

  const sim = createSim(world);
  // Place skater facing downhill (-x) on the 20% Potrero Hill ramp
  sim.place(80, 0, Math.PI, 4);
  for (let i = 0; i < 30; i++) sim.step(1 / 60, { steer: 0, push: 0, brake: 0, boost: false }, 0);
  assert.equal(sim.onRoad, true);
  assert.ok(sim.speed > 5.0, `skater should accelerate downhill on 20% grade, got speed=${sim.speed}`);
});
