#!/usr/bin/env swift
// mic_active — prints "YES" if any physical microphone has active I/O, "NO" otherwise.
// Used by meeting_bar.py to detect active calls (Teams, etc.)
// Ignores virtual devices: BlackHole, ZoomAudioDevice, Microsoft Teams Audio, etc.
import CoreAudio

let virtualDeviceNames = ["blackhole", "zoomaudiodevice", "microsoft teams audio",
                          "record and play", "aggregate", "multi-output"]

var propertySize: UInt32 = 0
var address = AudioObjectPropertyAddress(
    mSelector: kAudioHardwarePropertyDevices,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain
)

guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propertySize) == noErr else {
    print("NO")
    exit(0)
}

let deviceCount = Int(propertySize) / MemoryLayout<AudioDeviceID>.size
var deviceIDs = [AudioDeviceID](repeating: 0, count: deviceCount)
guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propertySize, &deviceIDs) == noErr else {
    print("NO")
    exit(0)
}

for id in deviceIDs {
    // Check if device has input channels
    var inputAddress = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreamConfiguration,
        mScope: kAudioDevicePropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain
    )
    var inputSize: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &inputAddress, 0, nil, &inputSize) == noErr, inputSize > 0 else {
        continue
    }
    let bufferListPtr = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: Int(inputSize))
    defer { bufferListPtr.deallocate() }
    guard AudioObjectGetPropertyData(id, &inputAddress, 0, nil, &inputSize, bufferListPtr) == noErr else {
        continue
    }
    let inputChannels = UnsafeMutableAudioBufferListPointer(bufferListPtr).reduce(0) { $0 + Int($1.mNumberChannels) }
    guard inputChannels > 0 else { continue }  // Not an input device

    // Get name
    var nameAddress = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var name: CFString = "" as CFString
    var nameSize = UInt32(MemoryLayout<CFString>.size)
    AudioObjectGetPropertyData(id, &nameAddress, 0, nil, &nameSize, &name)
    let nameStr = (name as String).lowercased()

    // Skip virtual devices
    if virtualDeviceNames.contains(where: { nameStr.contains($0) }) {
        continue
    }

    // Check running state
    var runningAddress = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var isRunning: UInt32 = 0
    var runningSize = UInt32(MemoryLayout<UInt32>.size)
    guard AudioObjectGetPropertyData(id, &runningAddress, 0, nil, &runningSize, &isRunning) == noErr else {
        continue
    }

    if isRunning == 1 {
        print("YES")
        exit(0)
    }
}

print("NO")
