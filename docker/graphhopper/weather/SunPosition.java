package com.graphhopper.routing.weather;

/**
 * Where the sun stands, seen from a point on the ground at a moment: altitude above the
 * horizon and azimuth clockwise from north, both in degrees. The low-precision formulas of the
 * Astronomical Almanac (about 0.01° between 1950 and 2050), without refraction: well inside
 * what a 10° horizon sector and a terrain model can tell apart.
 */
public final class SunPosition {
    public final double altitude, azimuth;

    private SunPosition(double altitude, double azimuth) {
        this.altitude = altitude;
        this.azimuth = azimuth;
    }

    public static SunPosition at(double lat, double lon, long epochMs) {
        // Days since J2000.0 (2000-01-01 12:00 UTC).
        double n = epochMs / 86_400_000.0 - 10_957.5;
        double meanLongitude = Math.toRadians(normalize(280.460 + 0.9856474 * n));
        double meanAnomaly = Math.toRadians(normalize(357.528 + 0.9856003 * n));
        double eclipticLongitude = meanLongitude + Math.toRadians(1.915 * Math.sin(meanAnomaly) + 0.020 * Math.sin(2 * meanAnomaly));
        double obliquity = Math.toRadians(23.439 - 0.0000004 * n);
        double rightAscension = Math.atan2(Math.cos(obliquity) * Math.sin(eclipticLongitude), Math.cos(eclipticLongitude));
        double declination = Math.asin(Math.sin(obliquity) * Math.sin(eclipticLongitude));
        double siderealTime = Math.toRadians(normalize(280.46061837 + 360.98564736629 * n + lon));
        double hourAngle = siderealTime - rightAscension;
        double phi = Math.toRadians(lat);
        double altitude = Math.asin(Math.sin(phi) * Math.sin(declination)
                + Math.cos(phi) * Math.cos(declination) * Math.cos(hourAngle));
        double azimuth = Math.atan2(-Math.cos(declination) * Math.sin(hourAngle),
                Math.sin(declination) * Math.cos(phi) - Math.cos(declination) * Math.sin(phi) * Math.cos(hourAngle));
        return new SunPosition(Math.toDegrees(altitude), normalize(Math.toDegrees(azimuth)));
    }

    private static double normalize(double degrees) {
        double value = degrees % 360;
        return value < 0 ? value + 360 : value;
    }
}
