/** 地图链接层：把已验证坐标交给百度网页标点，保留历史坐标系。 */
import type { GeoPoint } from './api/documents'

/** 百度标点链接函数：纬度在前，经度在后；旧高德坐标显式声明为GCJ-02。 */
export function baiduMapLink(point: GeoPoint, name: string, address?: string | null): string {
  const params = new URLSearchParams({
    location: `${point.latitude},${point.longitude}`, title: name, content: address || name,
    coord_type: point.coordinate_system === 'BD-09' ? 'bd09ll' : 'gcj02',
    output: 'html', src: 'webapp.TravelMindAI.TravelMindAI',
  })
  return `https://api.map.baidu.com/marker?${params}`
}
