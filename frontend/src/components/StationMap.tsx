import { useEffect, useRef, useState } from 'react';
import {
  Map,
  Marker,
  NavigationControl,
  AttributionControl,
  setWorkerUrl,
  type GeoJSONSource,
} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { MapStation, Place, Point, Recommendation, Station } from '../lib/types';
import { freshness } from '../lib/presentation';
import { Icon } from './Icon';

setWorkerUrl(workerUrl);
const BASEMAP =
  import.meta.env.VITE_MAP_STYLE_URL ?? 'https://tiles.openfreemap.org/styles/positron';
interface Props {
  stations: (Station | Recommendation | MapStation)[];
  selected?: Station | MapStation;
  replayCounts?: Record<string, number | null>;
  inventory: 'bikes' | 'docks';
  highlighted: string[];
  origin?: Place;
  destination?: Place;
  pickMode: 'origin' | 'destination' | null;
  now: number;
  onSelect: (id: string) => void;
  onBounds: (bbox: string) => void;
  onPick: (point: Point) => void;
}

export function StationMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<Map>();
  const latest = useRef(props);
  latest.current = props;
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(true);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!container.current) return;
    setReady(false);
    setFailed(false);
    setBusy(true);
    let map: Map;
    let boundTimer: ReturnType<typeof setTimeout>;
    let loaded = false;
    try {
      map = new Map({
        container: container.current,
        style: BASEMAP,
        center: [-73.986, 40.751],
        zoom: 13.4,
        maxZoom: 18,
        minZoom: 9,
        attributionControl: false,
      });
      instance.current = map;
    } catch {
      setFailed(true);
      return;
    }
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right');
    map.on('idle', () => setBusy(false));
    map.on('movestart', () => setBusy(true));
    map.on('sourcedataloading', () => setBusy(true));
    map.addControl(new AttributionControl({ compact: false }), 'bottom-right');
    map.on('error', () => {
      if (!loaded) setFailed(true);
    });
    const startupTimeout = setTimeout(() => {
      if (!loaded) setFailed(true);
    }, 20000);
    map.on('load', () => {
      loaded = true;
      clearTimeout(startupTimeout);
      setFailed(false);
      map.addSource('stations', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
        cluster: true,
        clusterMaxZoom: 13,
        clusterRadius: 44,
      });
      map.addLayer({
        id: 'clusters',
        type: 'circle',
        source: 'stations',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': '#234f43',
          'circle-radius': ['step', ['get', 'point_count'], 22, 20, 27, 50, 32],
          'circle-stroke-color': '#fffdf7',
          'circle-stroke-width': 3,
        },
      });
      map.addLayer({
        id: 'cluster-count',
        type: 'symbol',
        source: 'stations',
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 13,
        },
        paint: { 'text-color': '#ffffff' },
      });
      map.addLayer({
        id: 'station-halo',
        type: 'circle',
        source: 'stations',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-radius': 23,
          'circle-color': '#b6cf4a',
          'circle-opacity': ['case', ['get', 'highlighted'], 0.65, 0],
        },
      });
      map.addLayer({
        id: 'station-points',
        type: 'circle',
        source: 'stations',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-radius': 16,
          'circle-color': [
            'case',
            ['get', 'unavailable'],
            '#747e7a',
            ['get', 'replay'],
            [
              'interpolate',
              ['linear'],
              ['get', 'count'],
              0,
              '#61736a',
              3,
              '#497458',
              8,
              '#256250',
              20,
              '#173a31',
            ],
            ['==', ['get', 'count'], 0],
            '#b3503d',
            ['<', ['get', 'count'], 4],
            '#996320',
            '#256250',
          ],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2.5,
        },
      });
      map.addLayer({
        id: 'station-count',
        type: 'symbol',
        source: 'stations',
        filter: ['!', ['has', 'point_count']],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 12,
          'text-allow-overlap': true,
        },
        paint: { 'text-color': '#ffffff' },
      });
      map.on('click', async (event) => {
        if (latest.current.pickMode) {
          latest.current.onPick({ latitude: event.lngLat.lat, longitude: event.lngLat.lng });
          return;
        }
        const feature = map.queryRenderedFeatures(event.point, {
          layers: ['station-points', 'clusters'],
        })[0];
        if (!feature) return;
        if (feature.properties.cluster) {
          try {
            const zoom = await (map.getSource('stations') as GeoJSONSource).getClusterExpansionZoom(
              Number(feature.properties.cluster_id),
            );
            if (feature.geometry.type === 'Point')
              map.easeTo({ center: feature.geometry.coordinates as [number, number], zoom });
          } catch {
            /* Removed maps and changed clusters do not need a retry. */
          }
        } else latest.current.onSelect(String(feature.properties.id));
      });
      for (const layer of ['station-points', 'clusters']) {
        map.on('mouseenter', layer, () => {
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', layer, () => {
          map.getCanvas().style.cursor = latest.current.pickMode ? 'crosshair' : '';
        });
      }
      setReady(true);
    });
    const bounds = () => {
      clearTimeout(boundTimer);
      boundTimer = setTimeout(() => {
        const box = map.getBounds();
        latest.current.onBounds(
          [box.getWest(), box.getSouth(), box.getEast(), box.getNorth()]
            .map((value) => value.toFixed(5))
            .join(','),
        );
      }, 350);
    };
    map.on('moveend', bounds);
    const resize = new ResizeObserver(() => map.resize());
    resize.observe(container.current);
    return () => {
      clearTimeout(startupTimeout);
      clearTimeout(boundTimer);
      resize.disconnect();
      map.remove();
      instance.current = undefined;
    };
  }, [revision]);
  useEffect(() => {
    const map = instance.current;
    if (!ready || !map) return;
    const stations = [...props.stations];
    if (
      props.selected &&
      !stations.some((station) => station.external_id === props.selected!.external_id)
    )
      stations.push(props.selected);
    setBusy(true);
    (map.getSource('stations') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: stations.map((station) => {
        const live = 'bikes' in station ? station : undefined;
        const count = props.replayCounts
          ? props.replayCounts[station.external_id]
          : live?.[props.inventory];
        const operational =
          live &&
          live.is_installed !== false &&
          (props.inventory === 'bikes' ? live.is_renting : live.is_returning) !== false;
        const unavailable = props.replayCounts
          ? count == null
          : !live || freshness(live, props.now) !== 'fresh' || !operational || live.is_demo;
        return {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [station.longitude, station.latitude] },
          properties: {
            id: station.external_id,
            replay: !!props.replayCounts,
            count,
            label: unavailable ? '–' : String(count),
            unavailable,
            highlighted: props.highlighted.includes(station.external_id),
          },
        };
      }),
    });
  }, [
    ready,
    props.stations,
    props.selected,
    props.inventory,
    props.highlighted,
    props.now,
    props.replayCounts,
  ]);
  const selectedId = props.selected?.external_id;
  useEffect(() => {
    const map = instance.current;
    const station = latest.current.selected;
    if (!ready || !map || !station) return;
    const element = document.createElement('div');
    element.className = 'selected-map-ring';
    element.setAttribute('aria-hidden', 'true');
    const marker = new Marker({ element })
      .setLngLat([station.longitude, station.latitude])
      .addTo(map);
    map.easeTo({
      center: [station.longitude, station.latitude],
      zoom: Math.max(map.getZoom(), 14.2),
      padding: {
        right: !latest.current.replayCounts && window.innerWidth > 1100 ? 370 : 0,
        left: 0,
        top: 0,
        bottom: 0,
      },
      duration: 550,
    });
    return () => {
      marker.remove();
    };
  }, [ready, selectedId]);
  useEffect(() => {
    const map = instance.current;
    if (!map || !ready) return;
    const markers: Marker[] = [];
    for (const [place, text] of [
      [props.origin, 'A'],
      [props.destination, 'B'],
    ] as const) {
      if (!place) continue;
      const element = document.createElement('div');
      element.className = `journey-pin pin-${text}`;
      element.textContent = text;
      markers.push(new Marker({ element }).setLngLat([place.longitude, place.latitude]).addTo(map));
    }
    return () => markers.forEach((marker) => marker.remove());
  }, [ready, props.origin, props.destination]);
  useEffect(() => {
    if (instance.current)
      instance.current.getCanvas().style.cursor = props.pickMode ? 'crosshair' : '';
  }, [props.pickMode]);
  return (
    <>
      <div
        ref={container}
        className="station-map"
        aria-busy={busy}
        aria-label="Interactive bike-share station map. Use the station list for keyboard selection."
      />
      {!ready && !failed && (
        <div className="map-loading">
          <span className="spinner" />
          Loading the city map…
        </div>
      )}
      {failed && (
        <div className="map-failure" role="status">
          <Icon name="pin" />
          <strong>Basemap unavailable</strong>
          <p>
            {props.replayCounts
              ? 'The timeline, station list and charts still work.'
              : 'Your station list and planner still work.'}{' '}
            Map tiles need a connection and WebGL.
          </p>
          <button className="secondary-button" onClick={() => setRevision((value) => value + 1)}>
            Retry map
          </button>
        </div>
      )}
      <button
        className="map-reset"
        aria-label="Return map to Midtown Manhattan"
        onClick={() =>
          instance.current?.easeTo({
            center: [-73.986, 40.751],
            zoom: 13.4,
            padding: { right: 0, left: 0, top: 0, bottom: 0 },
          })
        }
      >
        <Icon name="pin" size={16} /> Midtown
      </button>
    </>
  );
}
