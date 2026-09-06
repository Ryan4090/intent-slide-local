import AppKit
import Foundation
import WebKit

private let maxSlideCount = 500
private let maxSlideDimension = 8_192.0
private let maxSingleSlidePixels = 16_000_000.0
private let maxSnapshotPixels = 64_000_000.0
private let maxCanvasPixels = 40_000_000.0
private let maxCanvasDimension = 32_768.0
private let thumbnailWidth = 480.0
private let labelHeight = 26.0
private let cellGap = 12.0

private struct ContactSheetGeometry {
    let columns: Int
    let rows: Int
    let thumbnailHeight: Double
    let canvasWidth: Double
    let canvasHeight: Double

    var canvasPixels: Double { canvasWidth * canvasHeight }
}

private func validatedGeometry(
    count: Int,
    width: Double,
    height: Double
) -> ContactSheetGeometry? {
    guard count > 0,
          count <= maxSlideCount,
          width.isFinite,
          height.isFinite,
          width >= 1,
          height >= 1,
          width <= maxSlideDimension,
          height <= maxSlideDimension else {
        return nil
    }
    let aspect = width / height
    let slidePixels = width * height
    guard aspect >= 0.5,
          aspect <= 4.0,
          slidePixels <= maxSingleSlidePixels,
          slidePixels * Double(count) <= maxSnapshotPixels else {
        return nil
    }
    let columns = min(count, max(1, Int(ceil(sqrt(Double(count) * aspect)))))
    let rows = Int(ceil(Double(count) / Double(columns)))
    let thumbHeight = thumbnailWidth / aspect
    let cellHeight = thumbHeight + labelHeight
    let canvasWidth = cellGap + Double(columns) * (thumbnailWidth + cellGap)
    let canvasHeight = cellGap + Double(rows) * (cellHeight + cellGap)
    guard canvasWidth.isFinite,
          canvasHeight.isFinite,
          canvasWidth <= maxCanvasDimension,
          canvasHeight <= maxCanvasDimension,
          canvasWidth * canvasHeight <= maxCanvasPixels else {
        return nil
    }
    return ContactSheetGeometry(
        columns: columns,
        rows: rows,
        thumbnailHeight: thumbHeight,
        canvasWidth: canvasWidth,
        canvasHeight: canvasHeight
    )
}

final class PreviewSnapshotter: NSObject, WKNavigationDelegate {
    private let webView: WKWebView
    private let window: NSWindow
    private let outputFile: URL
    private let slideImagesDirectory: URL?
    private var ownsSlideImagesDirectory = false
    private var index = 0
    private var total = 0
    private var slideWidth: CGFloat = 960
    private var slideHeight: CGFloat = 540
    private var pageImageWidth: CGFloat { max(1920, slideWidth * 2) }
    private var snapshots: [NSImage] = []

    init(htmlURL: URL, outputFile: URL, slideImagesDirectory: URL? = nil) {
        self.outputFile = outputFile
        self.slideImagesDirectory = slideImagesDirectory
        self.webView = WKWebView(frame: NSRect(x: 0, y: 0, width: 960, height: 540))
        self.window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 960, height: 540),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        super.init()
        if let directory = slideImagesDirectory {
            guard !FileManager.default.fileExists(atPath: directory.path) else {
                fail("slide image directory must be a new path")
            }
            do {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                ownsSlideImagesDirectory = true
            } catch { fail("slide image directory creation failed: \(error)") }
        }
        self.window.contentView = self.webView
        self.window.orderOut(nil)
        self.webView.navigationDelegate = self
        self.webView.loadFileURL(
            htmlURL,
            allowingReadAccessTo: htmlURL.deletingLastPathComponent()
        )
    }

    private func fail(_ message: String) -> Never {
        fputs("quicklook-render-error: \(message)\n", stderr)
        try? FileManager.default.removeItem(at: outputFile)
        if ownsSlideImagesDirectory, let directory = slideImagesDirectory {
            try? FileManager.default.removeItem(at: directory)
        }
        exit(1)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("navigation failed: \(error)")
    }

    func webView(
        _ webView: WKWebView,
        didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        fail("provisional navigation failed: \(error)")
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let probe = """
        (() => {
          const slides = Array.from(document.querySelectorAll('div.slide'));
          if (!slides.length) return null;
          const first = slides[0];
          const rect = first.getBoundingClientRect();
          const width = first.offsetWidth || rect.width;
          const height = first.offsetHeight || rect.height;
          return JSON.stringify({count: slides.length, width, height});
        })();
        """
        webView.evaluateJavaScript(probe) { result, error in
            guard error == nil,
                  let payload = result as? String,
                  let data = payload.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let count = object["count"] as? NSNumber,
                  let width = object["width"] as? NSNumber,
                  let height = object["height"] as? NSNumber else {
                self.fail("invalid slide probe: \(String(describing: result)) \(String(describing: error))")
            }
            let n = count.intValue
            let rawWidth = width.doubleValue
            let rawHeight = height.doubleValue
            guard validatedGeometry(count: n, width: rawWidth, height: rawHeight) != nil else {
                self.fail("unsupported slide geometry: count=\(n) width=\(rawWidth) height=\(rawHeight)")
            }
            if self.slideImagesDirectory != nil {
                // The requested pixel width is converted to points below, so
                // this allocation bound remains independent of the display.
                let scale = max(1920.0, rawWidth * 2) / rawWidth
                let pixels = rawWidth * rawHeight * scale * scale
                guard pixels <= maxSingleSlidePixels,
                      pixels * Double(n) <= maxSnapshotPixels else {
                    self.fail("high-resolution snapshot exceeds geometry bounds")
                }
            }
            let w = CGFloat(rawWidth)
            let h = CGFloat(rawHeight)
            self.total = n
            self.slideWidth = w
            self.slideHeight = h
            let size = NSSize(width: w, height: h)
            self.webView.frame = NSRect(origin: .zero, size: size)
            self.window.setContentSize(size)
            self.captureNext()
        }
    }

    private func captureNext() {
        if index >= total {
            writeContactSheet()
            print("rendered \(total) slides to \(outputFile.path)")
            NSApplication.shared.terminate(nil)
            return
        }
        let javascript = """
        (() => {
          const slides = Array.from(document.querySelectorAll('div.slide'));
          document.documentElement.style.margin = '0';
          document.documentElement.style.padding = '0';
          document.documentElement.style.width = '\(slideWidth)px';
          document.documentElement.style.height = '\(slideHeight)px';
          document.body.style.margin = '0';
          document.body.style.padding = '0';
          document.body.style.width = '\(slideWidth)px';
          document.body.style.height = '\(slideHeight)px';
          document.body.style.overflow = 'hidden';
          slides.forEach((slide, i) => {
            slide.style.display = i === \(index) ? 'block' : 'none';
            if (i === \(index)) {
              slide.style.position = 'relative';
              slide.style.top = '0';
              slide.style.left = '0';
              slide.style.margin = '0';
              slide.style.boxShadow = 'none';
            }
          });
          window.scrollTo(0, 0);
          return slides.length;
        })();
        """
        webView.evaluateJavaScript(javascript) { _, error in
            guard error == nil else {
                self.fail("javascript failed on slide \(self.index + 1): \(String(describing: error))")
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                let config = WKSnapshotConfiguration()
                config.rect = NSRect(
                    x: 0,
                    y: 0,
                    width: self.slideWidth,
                    height: self.slideHeight
                )
                if self.slideImagesDirectory != nil {
                    config.snapshotWidth = NSNumber(value: Double(self.pageImageWidth / max(1, self.window.backingScaleFactor)))
                }
                self.webView.takeSnapshot(with: config) { image, error in
                    guard error == nil, let image else {
                        self.fail("snapshot failed on slide \(self.index + 1): \(String(describing: error))")
                    }
                    if let directory = self.slideImagesDirectory {
                        self.writeSlideImage(image, to: directory.appendingPathComponent(String(format: "P%02d.png", self.index + 1)))
                    }
                    self.snapshots.append(image)
                    self.index += 1
                    self.captureNext()
                }
            }
        }
    }

    private func writeSlideImage(_ image: NSImage, to url: URL) {
        let width = Int(pageImageWidth.rounded())
        let height = Int((pageImageWidth * slideHeight / slideWidth).rounded())
        // Verify the actual snapshot pixels before drawing: allocating a larger
        // output bitmap must never disguise a low-resolution source snapshot.
        guard let captured = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
              abs(captured.width - width) <= 1,
              abs(captured.height - height) <= 1,
              Double(captured.width) * Double(captured.height) <= maxSingleSlidePixels else {
            fail("snapshot pixel dimensions do not match requested slide image")
        }
        print("snapshot pixels \(captured.width)x\(captured.height)")
        guard Double(width) * Double(height) <= maxSingleSlidePixels,
              let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height,
                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                  colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
              let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
            fail("slide image bitmap allocation failed")
        }
        bitmap.size = NSSize(width: width, height: height)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        image.draw(in: NSRect(x: 0, y: 0, width: width, height: height), from: .zero, operation: .copy, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
        guard let png = bitmap.representation(using: .png, properties: [:]) else {
            fail("slide image PNG encoding failed")
        }
        do { try png.write(to: url, options: .atomic) }
        catch { fail("slide image write failed: \(error)") }
    }

    private func writeContactSheet() {
        guard snapshots.count == total else {
            fail("snapshot count mismatch: \(snapshots.count)/\(total)")
        }
        guard let geometry = validatedGeometry(
            count: total,
            width: Double(slideWidth),
            height: Double(slideHeight)
        ) else {
            fail("unsupported contact-sheet geometry")
        }
        let columns = geometry.columns
        let thumbWidth = CGFloat(thumbnailWidth)
        let thumbHeight = CGFloat(geometry.thumbnailHeight)
        let labelHeight = CGFloat(labelHeight)
        let gap = CGFloat(cellGap)
        let cellHeight = thumbHeight + labelHeight
        let canvasWidth = CGFloat(geometry.canvasWidth)
        let canvasHeight = CGFloat(geometry.canvasHeight)
        // NSImage.lockFocus inherits the active display's backing scale. Allocate
        // actual PNG pixels so the file contract remains the same on Retina.
        guard let bitmap = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(canvasWidth.rounded()),
            pixelsHigh: Int(canvasHeight.rounded()),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else {
            fail("contact-sheet bitmap allocation failed")
        }
        bitmap.size = NSSize(width: canvasWidth, height: canvasHeight)
        guard let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
            fail("contact-sheet graphics context failed")
        }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        NSColor(calibratedRed: 0.87, green: 0.90, blue: 0.93, alpha: 1).setFill()
        NSBezierPath(rect: NSRect(x: 0, y: 0, width: canvasWidth, height: canvasHeight)).fill()
        let labelAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 16, weight: .semibold),
            .foregroundColor: NSColor(calibratedRed: 0.09, green: 0.23, blue: 0.37, alpha: 1),
        ]
        for (position, image) in snapshots.enumerated() {
            let column = position % columns
            let row = position / columns
            let x = gap + CGFloat(column) * (thumbWidth + gap)
            let y = canvasHeight - gap - CGFloat(row + 1) * cellHeight - CGFloat(row) * gap
            image.draw(
                in: NSRect(x: x, y: y + labelHeight, width: thumbWidth, height: thumbHeight),
                from: .zero,
                operation: .copy,
                fraction: 1
            )
            let label = String(format: "P%02d", position + 1)
            label.draw(at: NSPoint(x: x + 4, y: y + 3), withAttributes: labelAttributes)
        }
        NSGraphicsContext.restoreGraphicsState()

        guard let png = bitmap.representation(using: .png, properties: [:]) else {
            fail("contact-sheet PNG encoding failed")
        }
        do {
            try FileManager.default.createDirectory(
                at: outputFile.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try png.write(to: outputFile, options: .atomic)
        } catch {
            fail("contact-sheet write failed: \(error)")
        }
    }
}

if CommandLine.arguments.count == 5,
   CommandLine.arguments[1] == "--validate-geometry" {
    guard let count = Int(CommandLine.arguments[2]),
          let width = Double(CommandLine.arguments[3]),
          let height = Double(CommandLine.arguments[4]),
          let geometry = validatedGeometry(count: count, width: width, height: height) else {
        fputs(
            "quicklook-render-error: unsupported slide geometry: count=\(CommandLine.arguments[2]) width=\(CommandLine.arguments[3]) height=\(CommandLine.arguments[4])\n",
            stderr
        )
        exit(1)
    }
    let payload: [String: Any] = [
        "columns": geometry.columns,
        "rows": geometry.rows,
        "canvas_width": geometry.canvasWidth,
        "canvas_height": geometry.canvasHeight,
        "canvas_pixels": geometry.canvasPixels,
    ]
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
    exit(0)
}

let hasSlideImages = CommandLine.arguments.count == 5 && CommandLine.arguments[3] == "--slide-images-dir"
guard CommandLine.arguments.count == 3 || hasSlideImages else {
    fputs("usage: swift render_quicklook_contact_sheet.swift PREVIEW_HTML OUTPUT_PNG [--slide-images-dir DIRECTORY]\n", stderr)
    exit(2)
}

let htmlURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let slideImagesDirectory = hasSlideImages ? URL(fileURLWithPath: CommandLine.arguments[4]) : nil
let controller = PreviewSnapshotter(htmlURL: htmlURL, outputFile: outputURL, slideImagesDirectory: slideImagesDirectory)
withExtendedLifetime(controller) {
    app.run()
}
